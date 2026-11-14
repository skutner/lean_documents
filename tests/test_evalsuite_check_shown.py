"""Checks of the checker `evalsuite/check_shown.py`, the new answer key of the checked-answers study.

Three kinds of test, so that the checker is not tested only by the code it relies on:

  (a) Hand-written cases: small event lists written by hand from the reading procedure of the amendments family, with
      the value, the needed set and the relevant set written down before the interpreter was run (`HAND_CASES`).
  (b) Stand-in answerers: functions below that write `verdicts.json` sheets for generated dossiers, run through the
      real runner with `--arms-file evalsuite/arms-shown.json` and then through `check_shown.py`; the class of every
      statement is asserted.
  (c) Properties and fuzz: the interpreter against the key, sufficiency under added events, arbitrary sheets, a link or
      an oversize file in place of the sheet, and the refusals.

`FAMILIES` is the one list of large families under test; add a family by one line when its module exports the
interface. Nothing here pins a count of statements or a kind of statement of a family.
"""
import contextlib
import copy
from fractions import Fraction
import hashlib
import importlib
import io
import itertools
import json
import os
from pathlib import Path
import random
import re
import shutil
import sys
import tempfile
import unittest
from unittest import mock
from collections import Counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'evalsuite'))
sys.path.insert(0, str(ROOT / 'tests'))
sys.dont_write_bytecode = True
import blindness  # noqa: E402
import check_shown  # noqa: E402
import compare  # noqa: E402
import generate  # noqa: E402
import generate_big  # noqa: E402
import run_blind  # noqa: E402
import scorecard  # noqa: E402
from evalsuite_campaign_fixture import Campaign  # noqa: E402


_FAMILIES_BEFORE = {}


def setUpModule():
    """The large families are registered for the tests of this module only; `tearDownModule` puts the list back."""
    _FAMILIES_BEFORE.update(generate.HARD_FAMILIES)
    generate_big.register()


# Two patches of `check_shown` for the length of this module's tests, undone in `tearDownModule`, and why:
#   * `analyse` is memoised. The needed set of a statement costs one run of the interpreter for each event of the
#     dossier (0.4 s a statement in minutes) and the tests build many `check_shown.Family` objects for the same dossier;
#     the memo is keyed on the interpreter's own functions, the events and the query (a test that replaces `answer` of a
#     stand-in module gets a new key). It makes the suite several times shorter and changes no result.
#   * `read_dependencies` reads a module without `depends` as having none, so that the little stand-in modules of these
#     tests need not each define it. The three families all export it (`DEPENDS_REQUIRED`), and the checker's own
#     refusal is tested with the original function.
# `PairingAmendments.test_the_unpatched_checker_gives_the_same_rows` runs a whole in-process campaign check with both
# functions restored, and compares it with what the patched ones gave.
_ANALYSE, _MEMO = check_shown.analyse, {}


def _memoised_analyse(module, query, events, deps=None):
    key = (module.answer, module.relevant, getattr(module, 'depends', None),
           hashlib.sha256(json.dumps(events, sort_keys=True).encode()).hexdigest(), json.dumps(query, sort_keys=True))
    if key not in _MEMO:
        _MEMO[key] = _ANALYSE(module, query, events, deps)
    return _MEMO[key]


check_shown.analyse = _memoised_analyse

# A family module must export `depends(events)` and the checker refuses one that does not (`test_a_family_without_depends
# is_refused` uses the checker's own function). Until the minutes family exports it, and for the little stand-in modules
# of these tests, a module without it is read here with no dependencies, so that everything else is still tested;
# `DEPENDS_REQUIRED` names the families that must export it, and a family is added to it when its author has.
_READ_DEPENDENCIES = check_shown.read_dependencies
DEPENDS_REQUIRED = ('amendments', 'minutes', 'succession')


def _read_dependencies(module, events):
    if not hasattr(module, 'depends'):
        return {event['id']: {kind: [] for kind in check_shown.DEPENDENCY_KINDS} for event in events}
    return _READ_DEPENDENCIES(module, events)


check_shown.read_dependencies = _read_dependencies

FAMILIES = ('amendments', 'minutes', 'succession')  # add a family here, by one line, once its module exports the interface
# The seeds each family is run on: a stand-in campaign (two reviews of every stand-in) takes about 15 seconds a seed,
# the property tests a few seconds, and the sweeps over seeds 1 to 8 (two reports each) a few seconds a seed.
# Succession costs about a minute of analysis for each dossier (1.2 s a statement), so its seeds overlap: the campaign
# seeds are among the property seeds, which are the sweep seeds, and each dossier is analysed once. Seed 1 is the plain
# case; seed 3 has a statement with an empty needed set (not checkable); seed 6 has statements whose closure adds a
# fixer and counts that need paragraphs beyond one for each needed event.
SEEDS = {'amendments': (1, 2, 3), 'minutes': (1, 2), 'succession': (1, 3)}
PROPERTY_SEEDS = {'amendments': (1, 2, 3, 4, 5), 'minutes': (1, 2, 3), 'succession': (1, 3, 6)}
SWEEP_SEEDS = {'amendments': (1, 2, 3, 4, 5, 6, 7, 8), 'minutes': (1, 2, 3), 'succession': (1, 3, 6)}
# The needed set of a statement is computed by removing every event in turn (0.4 s a statement in minutes, a few
# milliseconds in amendments). The checker's own computation is shared by the tests; a second, independent loop checks
# it on every statement of amendments and on every FOURTH distinct statement of minutes and every EIGHTH of succession.
INDEPENDENT_EVERY = {'amendments': 1, 'minutes': 4, 'succession': 8}
CLOSURE_CHANGED = {}   # family -> (statements whose closed needed set is larger than the removal set, statements)
UNANSWERABLE = {}       # seed of succession -> the number of statements (of both reports) whose true value its names do not resolve
EMPTY_NEEDED = {}       # (family, seed) -> the number of statements (of both reports) no single event of which decides
UNQUOTABLE = {}        # (family, seed) -> the number of statements (of both reports) that are not checkable for want of a quote
ARMS_FILE = ROOT / 'evalsuite/arms-shown.json'
TEST_ROOT = ROOT / 'tmp'
TEST_ROOT.mkdir(exist_ok=True)
TEST_RUN = Path(tempfile.mkdtemp(prefix='evalsuite-check-shown-tests-', dir=TEST_ROOT))
NONE = 'none'
ABSENT = object()


def tearDownModule():
    shutil.rmtree(TEST_RUN, ignore_errors=True)
    check_shown.analyse, check_shown.read_dependencies = _ANALYSE, _READ_DEPENDENCIES      # the patches below end here
    generate.HARD_FAMILIES.clear()
    generate.HARD_FAMILIES.update(_FAMILIES_BEFORE)      # the list as it was found, for the modules that follow


def fresh_folder():
    return Path(tempfile.mkdtemp(dir=TEST_RUN))


# ---------------------------------------------------------------------------------------------------------
# A generated dossier of a family, with everything a stand-in needs to know
# ---------------------------------------------------------------------------------------------------------

@contextlib.contextmanager
def sized(size):
    """The amendments family reads its size from BIG_AMENDMENTS_SIZE (unset means 1)."""
    saved = os.environ.get('BIG_AMENDMENTS_SIZE')
    os.environ.pop('BIG_AMENDMENTS_SIZE', None)
    if size != 1:
        os.environ['BIG_AMENDMENTS_SIZE'] = str(size)
    try:
        yield
    finally:
        os.environ.pop('BIG_AMENDMENTS_SIZE', None)
        if saved is not None:
            os.environ['BIG_AMENDMENTS_SIZE'] = saved


def about_succession(events, query, module=None, deps=None):
    """The events about the berth or the party of a succession statement and, closed under every kind of dependency, what
    they cannot be read without: what may make the value "not established" when it is removed."""
    def berths(e):          # the hand-written events carry `berth` and `children`, the generated ones `berths` too
        return (set(e.get('berths') or ()) | ({e['berth']} if 'berth' in e else set())
                | {c['berth'] for c in e.get('children', ())})

    def parties(e):
        return set(e.get('parties') or ()) | set(e.get('party_names') or ())
    starts = [e['id'] for e in events
              if (query.get('berth') is not None and query['berth'] in berths(e))
              or (query.get('party') is not None and query['party'] in parties(e))]
    deps = deps or (module or importlib.import_module('big_succession')).depends(events)
    seen, todo = set(starts), list(starts)
    while todo:
        one = todo.pop()
        for kind in ('name', 'amount', 'target'):
            for need in deps.get(one, {}).get(kind, ()):
                if need not in seen:
                    seen.add(need)
                    todo.append(need)
    return seen


class Built:
    """One dossier of a family at one seed, generated with the facts in its key. Cached."""
    cache = {}

    @classmethod
    def of(cls, family, seed, size=1):
        if (family, seed, size) not in cls.cache:
            cls.cache[(family, seed, size)] = cls(family, seed, size)
        return cls.cache[(family, seed, size)]

    def __init__(self, family, seed, size=1):
        with generate_big.facts_in_keys(), sized(size):
            self.case = generate.build_case(family, seed, 1)
        self.size = size
        self.completed = set()          # statements whose needed events alone do not give the value (see `basis`)
        self.family_name, self.key = family, self.case['key']
        self.facts = self.key['facts']
        self.module = importlib.import_module(self.facts['interpreter'])
        self.events, self.passages, self.names = self.facts['events'], self.facts['passages'], self.facts['names']
        self.rows = self.key['expectations']
        self.texts = {name[len('sources/'):]: text for name, text in self.case['files'].items()
                      if name.startswith('sources/')}
        self.sources = check_shown.Sources(self.texts)
        self.original = {name: check_shown.split_paragraphs(text) for name, text in self.texts.items()}
        # `about`: every event a paragraph tells or is headed with; `listed`: what a quote of it earns (a passage entry
        # with the role `titles` is a heading: it names a thing and states nothing)
        self.listed, self.about = {}, {}
        for passage in self.passages:
            spot = (passage['file'], passage['paragraph'])
            self.about.setdefault(spot, set()).update(passage['event_ids'])
            if passage['role'] not in check_shown.EARNS_NOTHING:
                self.listed.setdefault(spot, set()).update(passage['event_ids'])
        self.of_event = {}
        for passage in self.passages:
            if passage['file'] in check_shown.NOT_SHOWABLE_FILES or passage['role'] in check_shown.EARNS_NOTHING:
                continue
            for event in passage['event_ids']:
                spot = (passage['file'], passage['paragraph'])
                if spot not in self.of_event.setdefault(event, []):
                    self.of_event[event].append(spot)
        self.readers = sorted({name for row in self.rows for name in row.get('lure_states', {})})
        self.by_id = {event['id']: event for event in self.events}
        self._needed, self._quotes, self._deps = {}, {}, None
        self.family_obj = check_shown.Family(self.module, self.facts, family)

    def independent_needed(self, query):
        """Removal of each event in turn and the closure of the result under the `name` dependencies, written here and
        not taken from check_shown. Returns (by removal, closed)."""
        base = self.module.answer(query, self.events)
        removal = [e['id'] for e in self.events
                   if self.module.answer(query, [x for x in self.events if x is not e]) != base]
        deps = self.module.depends(self.events) if hasattr(self.module, 'depends') else {}
        closed, todo = set(removal), list(removal)
        while todo:
            for need in deps.get(todo.pop(), {}).get('name', ()):
                if need not in closed:
                    closed.add(need)
                    todo.append(need)
        return removal, [e['id'] for e in self.events if e['id'] in closed]

    def analysis(self, row):
        key = json.dumps(row['query'], sort_keys=True)
        if key not in self._needed:
            found = self.family_obj.analysis(row['query'])
            if len(self._needed) % INDEPENDENT_EVERY[self.family_name] == 0:
                assert (found['needed_by_removal'], found['needed']) == self.independent_needed(row['query']), row['query']
            self._needed[key] = found
        return self._needed[key]

    def needed(self, row):
        """The needed events: those whose removal changes the value, with what fixes how their paragraphs name their
        subject."""
        return self.analysis(row)['needed']

    def needed_by_removal(self, row):
        return self.analysis(row)['needed_by_removal']

    def anchor(self, event, row):
        """Whether an event is what a statement is about, or what that cannot be read without, so that without it the
        value is not established at all (spec 3.3, subset rule): the clause of the parameter (amendments), the award of
        the project and what its paragraph is named through (minutes), the events about the berth or the party and what
        they cannot be read without (succession)."""
        query = row['query']
        if self.family_name == 'succession':
            if self._deps is None:
                self._deps = self.module.depends(self.events)
            return event in about_succession(self.events, query, self.module, self._deps)
        if self.family_name == 'amendments':
            starts = [e['id'] for e in self.events if e['kind'] == 'parameter' and e.get('parameter') == query.get('parameter')]
        else:
            starts = [e['id'] for e in self.events if e['kind'] == 'award' and e.get('subject') == query.get('subject')]
        deps = self.module.depends(self.events) if hasattr(self.module, 'depends') else {}
        seen = set(starts)
        todo = list(starts)
        while todo:
            one = todo.pop()
            for kind in ('name', 'amount', 'target'):
                for need in deps.get(one, {}).get(kind, ()):
                    if need not in seen:
                        seen.add(need)
                        todo.append(need)
        return event in seen

    def relevant(self, row):
        return set(self.module.relevant(row['query'], self.events))

    def true(self, row):
        return self.module.answer(row['query'], self.events)

    def quote(self, spot):
        """A quote of a paragraph that the checker locates in this paragraph and in no other, or None."""
        if spot not in self._quotes:
            file, number = spot
            words = self.original[file][number].split()
            found = None
            for start in range(len(words)):
                for end in range(start + 8, min(len(words), start + 8 + 12) + 1):
                    text = ' '.join(words[start:end])
                    if len(check_shown.normalise(text)) >= check_shown.MIN_QUOTE:
                        if self.sources.locate(text) == ('located', spot):
                            found = text
                        break
                if found:
                    break
            if found is None and self.sources.quotable(spot):
                found = self.original[file][number]            # no window of eight words, but the paragraph is located
            self._quotes[spot] = found
        return self._quotes[spot]

    def quotable(self, event):
        return [spot for spot in self.of_event.get(event, []) if self.quote(spot)]

    def unquotable(self, row):
        """The needed events no quote of which is located (every paragraph that states one is too short, or occurs word
        for word elsewhere): the statement cannot be shown by any answer."""
        return [event for event in self.needed(row) if not self.quotable(event)]

    def checkable(self, row):
        return bool(self.needed(row)) and not self.unquotable(row)

    def cited(self, spots):
        out = set()
        for spot in spots:
            out |= self.listed.get(spot, set())
        return out

    def answer_on(self, row, ids):
        return self.module.answer(row['query'], [e for e in self.events if e['id'] in ids])

    def sufficient(self, row, spots):
        """Both conditions of spec 3.3 step 4, worked out here: the cited events contain every needed event and
        give the true value."""
        cited = self.cited(spots)
        return set(self.needed(row)) <= cited and self.answer_on(row, cited) == self.true(row)

    def basis(self, row, avoid=(), pick=0):
        """One paragraph per needed event, in the order of the events; paragraphs that tell an event of `avoid` are
        not used. `pick` chooses which of an event's paragraphs, counted from the end when negative. Where the needed
        events alone do not give the true value (any one of several provisions would do), paragraphs of relevant events
        are added until they do."""
        def options(event):
            return [s for s in self.quotable(event) if not (self.listed.get(s, set()) & set(avoid))]
        spots = []
        for event in self.needed(row):
            choices = options(event)
            if choices:
                spot = choices[pick % len(choices)]
                if spot not in spots:
                    spots.append(spot)
        truth = self.true(row)
        if self.answer_on(row, self.cited(spots)) != truth and not avoid:
            self.completed.add(json.dumps(row['query'], sort_keys=True))
            for event in sorted(self.relevant(row)):
                now = self.answer_on(row, self.cited(spots))
                if now == truth:
                    break
                # a provision and what it requires (its definition, its clause), if that moves the answer
                closure, todo = [], [event]
                while todo:
                    one = todo.pop()
                    if one not in closure:
                        closure.append(one)
                        # `requires` and `target` (amendments), `needs` and `ref` (succession: the passages an event is told
                        # through, and the event a register note or a correction is about)
                        todo += list(self.by_id[one].get('requires', ())) + list(self.by_id[one].get('needs', ())) + [
                            self.by_id[one][key] for key in ('target', 'ref') if key in self.by_id[one]]
                more = [options(one)[0] for one in closure if options(one) and options(one)[0] not in spots]
                if more and self.answer_on(row, self.cited(spots + more)) != now:
                    spots += more              # one provision per instrument, not the whole file
        return spots


def sheet_row(row, verdict=None, value=ABSENT, spots=None, built=None, quotes=None, extra=None):
    out = {'claim_id': row['claim_id'], 'semantic_state': verdict or row['semantic_state'],
           'formal_status': 'not_attempted', 'explanation': 'Checked it.'}
    if value is not ABSENT:
        out['value'] = value
    if spots is not None:
        out['evidence'] = [{'path': 'case/sources/' + file, 'quote': built.quote((file, number))}
                           for file, number in spots]
    if quotes is not None:
        out['evidence'] = [{'path': 'case/sources/' + file, 'quote': quote} for file, quote in quotes]
    out.update(extra or {})
    return out


def render(built, row, true, style=0):
    """A value in the form the note prescribes, written in one of a few ways the form allows."""
    form = row['value_form']
    kind = form if isinstance(form, str) else form[0]
    if true is None:
        return 'null'
    if true == NONE:
        return 'none'
    if kind == 'integer':
        return [int(true), f'{int(true):,}', f'€{int(true):,}', f'EUR {int(true)}'][style % 4]
    if kind == 'decimal':
        return [true, f'{true}%', str(true), f'{true:.2f}'][style % 4]
    if kind == 'name':
        names = built.names.get(true) or [true]
        return names[style % len(names)]
    return true


# ---------------------------------------------------------------------------------------------------------
# (a) Hand-written cases. Written by hand from the reading procedure of evalsuite/big_amendments.py: each case holds
# a small event list and, per query, the value, the needed set and the relevant set that were written down before the
# interpreter was run on them. Sets of event ids; 'none' is the word for "no value".
# ---------------------------------------------------------------------------------------------------------

E0 = '2031-01-01'


def par(i, key, term, kind, value, clause=1, party=None):
    return {'id': i, 'kind': 'parameter', 'parameter': key, 'term': term, 'heading': term, 'clause': clause,
            'value_kind': kind, 'value': value, 'from': E0, 'party': party, 'decides': True}


def defn(i, key, term, param):
    return {'id': i, 'kind': 'definition', 'source': 'agreement', 'term': term, 'parameter': key, 'date': E0,
            'paragraph': 0, 'requires': [param], 'decides': True}


def amend(i, key, date, paragraph, op, value, commence, requires, category=None, sunset=None, ends=None,
          link=None, style='heading', definition=None, number=None):
    return {'id': i, 'kind': 'amendment', 'instrument': 'Deed of Variation dated ' + date, 'date': date,
            'paragraph': paragraph, 'parameter': key, 'operation': op, 'category': category, 'value': value,
            'commencement': commence, 'link': link, 'sunset': sunset, 'ends': ends,
            'reference': {'style': style, 'definition': definition, 'number': number}, 'requires': requires,
            'decides': True}


def on(day):
    return {'type': 'date', 'date': day}


def at_event(event):
    return {'type': 'event', 'event': event}


def revoke(i, key, date, paragraph, target, commence, requires):
    return {'id': i, 'kind': 'revocation', 'instrument': 'Deed of Variation dated ' + date, 'date': date,
            'paragraph': paragraph, 'target': target, 'parameter': key, 'commencement': commence,
            'requires': requires, 'decides': True}


def sunset_later(i, key, date, paragraph, target, sunset):
    return {'id': i, 'kind': 'sunset', 'instrument': 'Deed of Variation dated ' + date, 'date': date,
            'paragraph': paragraph, 'target': target, 'parameter': key, 'sunset': sunset, 'requires': [target],
            'decides': True}


def correct(i, key, date, paragraph, target, field, old, new):
    return {'id': i, 'kind': 'correction', 'instrument': 'Correction Slip dated ' + date, 'date': date,
            'paragraph': paragraph, 'target': target, 'parameter': key, 'field': field, 'old': old, 'new': new,
            'requires': [target], 'decides': True}


def record(i, date, paragraph, phrase):
    return {'id': i, 'kind': 'event', 'phrase': phrase, 'recorded_by': 'Certificate', 'date': date,
            'paragraph': paragraph, 'requires': [], 'decides': True}


def never(i, phrase):
    return {'id': i, 'kind': 'event', 'phrase': phrase, 'recorded_by': None, 'date': None, 'paragraph': None,
            'requires': [], 'decides': True}


def renumber(i, date, paragraph, first, last, shift, effective):
    return {'id': i, 'kind': 'renumbering', 'instrument': 'Deed of Variation dated ' + date, 'date': date,
            'paragraph': paragraph, 'first': first, 'last': last, 'shift': shift, 'effective': effective,
            'requires': [], 'decides': True}


def decoy(i, key, date, paragraph, value):
    return {'id': i, 'kind': 'decoy', 'instrument': 'Side Letter dated ' + date, 'date': date,
            'paragraph': paragraph, 'decides': False, 'requires': [], 'parameter': key, 'value': value,
            'phrase': None}


def define_short(i, key, date, paragraph, short, requires):
    return {'id': i, 'kind': 'definition', 'source': 'instrument', 'instrument': 'Deed of Variation dated ' + date,
            'date': date, 'paragraph': paragraph, 'term': short, 'parameter': key,
            'reference': {'style': 'term', 'definition': 'E002', 'number': None}, 'requires': requires,
            'decides': True}


def V(date, category=None):
    return {'kind': 'value', 'parameter': None, 'date': date, 'category': category}


HAND_CASES = []


def case(name, events, queries):
    HAND_CASES.append({'name': name, 'events': events, 'queries': queries})


case('superseded value', [      # a decoy and an unrelated parameter are present
    par('E001', 'cure_period', 'Cure Period', 'days', 30),
    defn('E002', 'cure_period', 'Cure Period', 'E001'),
    amend('E003', 'cure_period', '2031-03-10', 1, 'substitute', 45, on('2031-04-01'), ['E002'], style='term'),
    amend('E004', 'cure_period', '2031-09-15', 1, 'substitute', 60, on('2031-10-01'), ['E001']),
    decoy('E005', 'cure_period', '2031-12-01', 1, 99),
    par('E006', 'payment_period', 'Payment Period', 'days', 30),
    amend('E007', 'payment_period', '2031-04-05', 1, 'substitute', 10, on('2031-05-01'), ['E006']),
], [
    {'query': dict(V('2031-11-01'), parameter='cure_period'), 'value': 60,
     'needed': {'E001', 'E004'}, 'relevant': {'E001', 'E002', 'E003', 'E004', 'E005'}},
    {'query': dict(V('2031-06-01'), parameter='cure_period'), 'value': 45,
     'needed': {'E001', 'E002', 'E003'}, 'relevant': {'E001', 'E002', 'E003', 'E004', 'E005'}},
])

case('revoked provision', [
    par('E001', 'payment_period', 'Payment Period', 'days', 30),
    amend('E002', 'payment_period', '2031-01-20', 1, 'substitute', 45, on('2031-02-01'), ['E001']),
    revoke('E003', 'payment_period', '2031-06-01', 1, 'E002', on('2031-07-01'), ['E002']),
], [
    {'query': dict(V('2031-05-01'), parameter='payment_period'), 'value': 45,
     'needed': {'E001', 'E002'}, 'relevant': {'E001', 'E002', 'E003'}},
    {'query': dict(V('2031-08-01'), parameter='payment_period'), 'value': 30,
     'needed': {'E001', 'E003'}, 'relevant': {'E001', 'E002', 'E003'}},
    {'query': dict(V('2031-06-30'), parameter='payment_period'), 'value': 45,
     'needed': {'E001', 'E002'}, 'relevant': {'E001', 'E002', 'E003'}},
])

case('corrected figure', [
    par('E001', 'retention_rate', 'Retention Rate', 'pct', 5),
    amend('E002', 'retention_rate', '2031-02-01', 1, 'substitute', 10, on('2031-03-01'), ['E001']),
    correct('E003', 'retention_rate', '2031-04-01', 1, 'E002', 'value', 10, 7.5),
], [
    {'query': dict(V('2031-06-01'), parameter='retention_rate'), 'value': 7.5,
     'needed': {'E001', 'E002', 'E003'}, 'relevant': {'E001', 'E002', 'E003'}},
    {'query': dict(V('2031-02-15'), parameter='retention_rate'), 'value': 5,
     'needed': {'E001'}, 'relevant': {'E001', 'E002', 'E003'}},
])

case('restatement', [
    par('E001', 'late_payment_rate', 'Late Payment Rate', 'pct', 2),
    amend('E002', 'late_payment_rate', '2031-02-01', 1, 'substitute', 4, on('2031-02-01'), ['E001']),
    amend('E003', 'late_payment_rate', '2031-05-01', 1, 'substitute', 2, on('2031-05-01'), ['E001']),
], [
    {'query': dict(V('2031-06-01'), parameter='late_payment_rate'), 'value': 2,
     'needed': {'E001', 'E003'}, 'relevant': {'E001', 'E002', 'E003'}},
    {'query': {'kind': 'first', 'parameter': 'late_payment_rate', 'value': 2}, 'value': '2031-01-01',
     'needed': {'E001'}, 'relevant': {'E001', 'E002', 'E003'}},
    {'query': {'kind': 'differs', 'parameter': 'late_payment_rate', 'date': '2031-03-01', 'date2': '2031-06-01',
               'category': None}, 'value': 'different',
     'needed': {'E001', 'E002', 'E003'}, 'relevant': {'E001', 'E002', 'E003'}},
])

case('chain of values', [
    par('E001', 'delivery_grace_period', 'Delivery Grace Period', 'days', 10),
    par('E002', 'acceptance_test_period', 'Acceptance Test Period', 'days', 20),
    amend('E003', 'delivery_grace_period', '2031-02-01', 1, 'substitute', 14, on('2031-03-01'), ['E001']),
    amend('E004', 'acceptance_test_period', '2031-05-01', 1, 'substitute', None, on('2031-06-01'), ['E002', 'E001'],
          link={'parameter': 'delivery_grace_period', 'style': 'heading'}),
    amend('E005', 'delivery_grace_period', '2031-08-01', 1, 'substitute', 21, on('2031-09-01'), ['E001']),
], [
    {'query': dict(V('2031-10-01'), parameter='acceptance_test_period'), 'value': 14,
     'needed': {'E001', 'E002', 'E003', 'E004'}, 'relevant': {'E001', 'E002', 'E003', 'E004', 'E005'}},
    {'query': dict(V('2031-10-01'), parameter='delivery_grace_period'), 'value': 21,
     'needed': {'E001', 'E005'}, 'relevant': {'E001', 'E003', 'E005'}},
])

case('no value', [
    par('E001', 'failure_threshold', 'Failure Threshold', 'count', None),
    amend('E002', 'failure_threshold', '2031-05-01', 1, 'substitute', 5, on('2031-06-01'), ['E001']),
    par('E003', 'monthly_order_ceiling', 'Monthly Order Ceiling', 'money', 100000),
    amend('E004', 'monthly_order_ceiling', '2031-03-15', 1, 'disapply', None, on('2031-04-01'), ['E003'],
          ends='2031-04-30'),
], [
    {'query': dict(V('2031-03-01'), parameter='failure_threshold'), 'value': NONE,
     'needed': {'E001'}, 'relevant': {'E001', 'E002'}},
    {'query': dict(V('2031-04-15'), parameter='monthly_order_ceiling'), 'value': NONE,
     'needed': {'E003', 'E004'}, 'relevant': {'E003', 'E004'}},
    {'query': dict(V('2031-05-01'), parameter='monthly_order_ceiling'), 'value': 100000,
     'needed': {'E003'}, 'relevant': {'E003', 'E004'}},
    {'query': dict(V('2031-04-30'), parameter='monthly_order_ceiling'), 'value': NONE,
     'needed': {'E003', 'E004'}, 'relevant': {'E003', 'E004'}},
])

case('commencement on an event', [
    par('E001', 'cure_period', 'Cure Period', 'days', 30),
    record('E002', '2031-05-10', 1, 'the new service desk platform has gone live'),
    amend('E003', 'cure_period', '2031-02-01', 1, 'substitute', 60, at_event('E002'), ['E001', 'E002']),
    never('E004', 'the Eastern region rollout has been completed'),
    amend('E005', 'cure_period', '2031-02-01', 2, 'exception', 90, at_event('E004'), ['E001', 'E004'],
          category='Training Services'),
], [
    {'query': dict(V('2031-05-10'), parameter='cure_period'), 'value': 60,
     'needed': {'E001', 'E002', 'E003'}, 'relevant': {'E001', 'E002', 'E003', 'E004', 'E005'}},
    # Corrected on 14 November under spec version 3: without the record E002, E003 reads as if no instrument recorded
    # its event, so it never has effect and the value on 2031-05-09 is still 30; by the definition (removal changes
    # the value) E002 is not needed. First written as {E001, E002}.
    {'query': dict(V('2031-05-09'), parameter='cure_period'), 'value': 30,
     'needed': {'E001'}, 'relevant': {'E001', 'E002', 'E003', 'E004', 'E005'}},
    # Corrected on 14 November under spec version 3: without E004, E005 reads as never having effect, which is what
    # it does anyway, so the value for Training Services stays 60 and E004 is not needed. First written with E004.
    {'query': dict(V('2031-12-01', 'Training Services'), parameter='cure_period'), 'value': 60,
     'needed': {'E001', 'E002', 'E003'}, 'relevant': {'E001', 'E002', 'E003', 'E004', 'E005'}},
])

case('sunsets', [
    par('E001', 'delay_damages_rate', 'Delay Damages Rate', 'pct', 1),
    amend('E002', 'delay_damages_rate', '2031-01-15', 1, 'substitute', 2, on('2031-02-01'), ['E001'],
          sunset='2031-12-01'),
    sunset_later('E003', 'delay_damages_rate', '2031-06-01', 1, 'E002', '2031-09-01'),
], [
    {'query': dict(V('2031-08-31'), parameter='delay_damages_rate'), 'value': 2,
     'needed': {'E001', 'E002'}, 'relevant': {'E001', 'E002', 'E003'}},
    {'query': dict(V('2031-09-01'), parameter='delay_damages_rate'), 'value': 1,
     'needed': {'E001', 'E003'}, 'relevant': {'E001', 'E002', 'E003'}},
])

case('category and general', [
    par('E001', 'audit_response_period', 'Audit Response Period', 'days', 15),
    amend('E002', 'audit_response_period', '2031-01-10', 1, 'exception', 30, on('2031-02-01'), ['E001'],
          category='Hardware Services'),
    amend('E003', 'audit_response_period', '2031-05-15', 1, 'substitute', 20, on('2031-06-01'), ['E001']),
], [
    {'query': dict(V('2031-07-01', 'Hardware Services'), parameter='audit_response_period'), 'value': 20,
     'needed': {'E001', 'E003'}, 'relevant': {'E001', 'E002', 'E003'}},
    {'query': dict(V('2031-03-01', 'Hardware Services'), parameter='audit_response_period'), 'value': 30,
     'needed': {'E001', 'E002'}, 'relevant': {'E001', 'E002', 'E003'}},
    {'query': dict(V('2031-03-01'), parameter='audit_response_period'), 'value': 15,
     'needed': {'E001'}, 'relevant': {'E001', 'E002', 'E003'}},
])

case('renumbering', [
    par('E001', 'invoice_query_window', 'Invoice Query Window', 'days', 10, clause=25),
    renumber('E002', '2031-02-01', 1, 20, 40, 2, '2031-03-01'),
    amend('E003', 'invoice_query_window', '2031-04-01', 1, 'substitute', 30, on('2031-04-01'), ['E001', 'E002'],
          style='number', number=27),
], [
    {'query': dict(V('2031-05-01'), parameter='invoice_query_window'), 'value': 30,
     'needed': {'E001', 'E002', 'E003'}, 'relevant': {'E001', 'E002', 'E003'}},
])

case('count of instruments', [      # two provisions of one instrument for one parameter
    par('E001', 'milestone_default_threshold', 'Milestone Default Threshold', 'count', 2),
    amend('E002', 'milestone_default_threshold', '2031-02-01', 1, 'substitute', 3, on('2031-03-01'), ['E001']),
    amend('E003', 'milestone_default_threshold', '2031-02-01', 2, 'exception', 4, on('2031-03-01'), ['E001'],
          category='Software Services'),
    amend('E004', 'milestone_default_threshold', '2031-06-01', 1, 'substitute', 5, on('2031-07-01'), ['E001']),
], [
    {'query': {'kind': 'count', 'parameter': 'milestone_default_threshold', 'date': '2031-04-01'}, 'value': 1,
     'needed': {'E001'}, 'relevant': {'E001', 'E002', 'E003', 'E004'}},
    {'query': {'kind': 'count', 'parameter': 'milestone_default_threshold', 'date': '2031-08-01'}, 'value': 2,
     'needed': {'E001', 'E004'}, 'relevant': {'E001', 'E002', 'E003', 'E004'}},
])

case('named through a defined expression', [
    par('E001', 'site_limit', 'Site Limit', 'count', 4),
    defn('E002', 'site_limit', 'Site Limit', 'E001'),
    define_short('E003', 'site_limit', '2031-02-01', 1, 'the Interim Window', ['E002']),
    amend('E004', 'site_limit', '2031-03-01', 1, 'substitute', 6, on('2031-04-01'), ['E003'], style='short',
          definition='E003'),
], [
    {'query': dict(V('2031-05-01'), parameter='site_limit'), 'value': 6,
     'needed': {'E001', 'E002', 'E003', 'E004'}, 'relevant': {'E001', 'E002', 'E003', 'E004'}},
    {'query': dict(V('2031-03-15'), parameter='site_limit'), 'value': 4,
     'needed': {'E001'}, 'relevant': {'E001', 'E002', 'E003', 'E004'}},
])

# Two statements about the whole file (written on 14 November, after the first review, with the expectations set
# down before the interpreter was run on them).
# 13. "The number of amending provisions in the file, for all parameters together, that had taken effect on or before
# 2031-06-01": E003 (from 2031-03-01) and E004 (from 2031-05-01) took effect, E005 (from 2031-10-01) did not. Every
# counted provision is needed, and so is the clause each one is made through; E005 is not.
case('count over the whole file: every counted provision is needed', [
    par('E001', 'cure_period', 'Cure Period', 'days', 30),
    par('E002', 'payment_period', 'Payment Period', 'days', 30),
    amend('E003', 'cure_period', '2031-02-01', 1, 'substitute', 45, on('2031-03-01'), ['E001']),
    amend('E004', 'payment_period', '2031-04-01', 1, 'substitute', 10, on('2031-05-01'), ['E002']),
    amend('E005', 'cure_period', '2031-09-01', 1, 'substitute', 60, on('2031-10-01'), ['E001']),
], [
    {'query': {'kind': 'total', 'date': '2031-06-01'}, 'value': 2,
     'needed': {'E001', 'E002', 'E003', 'E004'}, 'relevant': {'E001', 'E002', 'E003', 'E004', 'E005'}},
])

# 14. "The number of parameters for which an amending provision had taken effect on or before 2031-06-01": the Cure
# Period has two (E003, E004), the Payment Period one (E005): two parameters. Either provision of the Cure Period
# would do, so neither is needed alone; the Payment Period's provision is, and so are the two clauses.
case('count over the whole file: one of several would do', [
    par('E001', 'cure_period', 'Cure Period', 'days', 30),
    par('E002', 'payment_period', 'Payment Period', 'days', 30),
    amend('E003', 'cure_period', '2031-02-01', 1, 'substitute', 45, on('2031-03-01'), ['E001']),
    amend('E004', 'cure_period', '2031-03-10', 1, 'substitute', 60, on('2031-04-01'), ['E001']),
    amend('E005', 'payment_period', '2031-04-01', 1, 'substitute', 10, on('2031-05-01'), ['E002']),
], [
    {'query': {'kind': 'params', 'date': '2031-06-01'}, 'value': 2,
     'needed': {'E001', 'E002', 'E005'}, 'relevant': {'E001', 'E002', 'E003', 'E004', 'E005'}},
])


# ---------------------------------------------------------------------------------------------------------
# Hand-written cases for the minutes family (14 November): small event lists written by hand from the reading rules of
# its procedure.md text, the value, the needed set and the relevant set set down before the interpreter was run on them.
# Version 1 (hash cb58e74e...) was run on the first module and agreed on all 28 queries. Version 2 (hash 0a7198cb...)
# was written to the ruling of spec 3.3 and section 12 (a name is a dependency; a count does not turn on amounts; a
# return paragraph earns the return) before it was run; the first module disagreed with it on four queries, all of
# them "a count does not turn on amounts"; nothing was aligned. Version 3 (hash f24be38b...) is version 2 with the
# dependencies written into the fields the new module records them in (`named_by`, `amount_by`; version 2 had put them
# in `refs`, which made three more queries "disagree" with the new module through the encoding alone); no expectation
# was changed between the three. The helpers fill fields and decide nothing.
# ---------------------------------------------------------------------------------------------------------

MA, MB, MC = 'Corbin Lane Hostel Refurbishment', 'Tanner Row Hall Extension', 'Quay Road Clinic Rewiring'
M_INDEX = {MA: 0, MB: 1, MC: 2, 'Mill Lane Schoolroom Kitchen Refit': 3}
M_DECIDES = {'intro': False, 'award': True, 'defer_award': False, 'vary': True, 'fell_vary': False, 'defer_vary': False,
           'correct': True, 'rescind': True, 'authorise': False, 'pay': True, 'return': True, 'restate': False,
           'outside': False, 'names': False}


def mD(m):
    return '%d-%02d-10' % (2036 + m // 12, m % 12 + 1)


def mev(i, kind, m, subject, refs=(), named_by=(), amount_by=(), **fields):
    out = {'id': 'T%04d' % i, 'kind': kind, 'm': m, 'date': mD(m), 'p': M_INDEX[subject], 'subject': subject,
           'decides': M_DECIDES[kind], 'refs': ['T%04d' % r for r in refs], 'since': None, 'full': True,
           'named_by': ['T%04d' % r for r in named_by], 'amount_by': ['T%04d' % r for r in amount_by]}
    out.update(fields)
    return out


def m_abs_pay(i, m, subject, amount):
    return mev(i, 'pay', m, subject, spec={'type': 'abs', 'amount': amount})


def mq(kind, subject=None, m=None):
    date = mD(m) if m is not None else None
    return {'kind': kind, 'subject': subject, 'until': date,
            'key': '%s|%s|%s' % (kind, subject or '', date or '')}


def mids(*numbers):
    return {'T%04d' % n for n in numbers}


MINUTES_CASES = []


def m_case(name, events, queries):
    MINUTES_CASES.append({'name': name, 'events': events, 'queries': queries})


# 1. A payment told as made to "the Marlow Trust scheme": it depends on the application (the intro event) that ties
# the sponsor to the project, so without the intro it is not a payment of the project at all (counted nowhere). The
# award and a sum from another body decide nothing about a total; another project's events are not relevant.
m_case('sponsor scheme', [
    mev(1, 'intro', 0, MA, sponsor='Marlow Trust'),
    mev(2, 'award', 1, MA, amount=12000),
    mev(3, 'pay', 2, MA, named_by=(1,), spec={'type': 'abs', 'amount': 3000}),
    m_abs_pay(4, 3, MA, 2000),
    mev(5, 'award', 1, MB, amount=8000),
    m_abs_pay(6, 2, MB, 1000),
    mev(7, 'outside', 2, MA, amount=9000),
], [
    {'query': mq('total', MA, 3), 'value': 5000, 'needed': mids(1, 3, 4), 'relevant': mids(1, 2, 3, 4, 7)},
    {'query': mq('count', MA, 3), 'value': 2, 'needed': mids(1, 3, 4), 'relevant': mids(1, 2, 3, 4, 7)},
])

# 2. A payment authorised at one meeting and made at a later one ("the payment authorised at the March meeting", the
# paragraph that points back): the report counts for the sum authorised.
m_case('authorised, then paid', [
    mev(1, 'award', 1, MA, amount=20000),
    mev(2, 'authorise', 2, MA, amount=5000),
    mev(3, 'pay', 3, MA, refs=(2,), spec={'type': 'authorised', 'auth': 'T0002'}),
    m_abs_pay(4, 4, MA, 4000),
], [
    {'query': mq('total', MA, 4), 'value': 9000, 'needed': mids(2, 3, 4), 'relevant': mids(1, 2, 3, 4)},
    {'query': mq('largest', MA, 4), 'value': 5000, 'needed': mids(2, 3), 'relevant': mids(1, 2, 3, 4)},
    {'query': mq('balance', MA, 4), 'value': 11000, 'needed': mids(1, 2, 3, 4), 'relevant': mids(1, 2, 3, 4)},
    # a count does not turn on amounts: the payment authorised is counted though the authorisation is not needed
    {'query': mq('count', MA, 4), 'value': 2, 'needed': mids(3, 4), 'relevant': mids(1, 2, 3, 4)},
])

# 3. Third in the order of backing, with an earlier award later rescinded: B (second) holds an award from March and
# none from May; D's award comes after C's. C's payment is told as made to "the third undertaking to receive the
# committee's backing": it depends on every award up to and including C's own (B's included, though rescinded), and on
# none after it. A deferred recommendation for C decides nothing.
m_case('order of backing, an earlier award rescinded', [
    mev(1, 'award', 1, MA, amount=10000),
    mev(2, 'award', 2, MB, amount=6000),
    mev(3, 'award', 3, MC, amount=9000),
    mev(4, 'rescind', 4, MB, refs=(2,)),
    mev(5, 'defer_award', 2, MC, amount=9000),
    mev(6, 'award', 5, 'Mill Lane Schoolroom Kitchen Refit', amount=7000),
    mev(7, 'pay', 6, MC, named_by=(1, 2, 3), spec={'type': 'abs', 'amount': 2500}),
], [
    {'query': mq('projects', None, 6), 'value': 3, 'needed': mids(1, 3, 4, 6), 'relevant': mids(1, 2, 3, 4, 6)},
    {'query': mq('projects', None, 2), 'value': 2, 'needed': mids(1, 2), 'relevant': mids(1, 2, 3, 4, 6)},
    {'query': mq('award', MC, 6), 'value': 9000, 'needed': mids(3), 'relevant': mids(1, 2, 3, 5, 7)},
    {'query': mq('total', MC, 6), 'value': 2500, 'needed': mids(1, 2, 3, 7), 'relevant': mids(1, 2, 3, 5, 7)},
    {'query': mq('count', MC, 6), 'value': 1, 'needed': mids(1, 2, 3, 7), 'relevant': mids(1, 2, 3, 5, 7)},
])

# 4. Amounts given as "the same figure as" another payment: the first instalment of the project, and the first
# instalment paid to another project.
m_case('same figure as another payment', [
    mev(1, 'award', 1, MA, amount=30000),
    m_abs_pay(2, 2, MA, 4000),
    mev(3, 'pay', 3, MA, refs=(2,), spec={'type': 'first', 'of': 'T0002'}),
    mev(4, 'award', 1, MB, amount=20000),
    mev(5, 'pay', 4, MB, refs=(2,), spec={'type': 'first_of', 'other': MA, 'of': 'T0002'}),
], [
    {'query': mq('total', MA, 3), 'value': 8000, 'needed': mids(2, 3), 'relevant': mids(1, 2, 3)},
    {'query': mq('total', MB, 4), 'value': 4000, 'needed': mids(2, 5), 'relevant': mids(2, 4, 5)},
    {'query': mq('largest', MA, 3), 'value': 4000, 'needed': mids(2), 'relevant': mids(1, 2, 3)},
    # the amount of the other project's payment is stated by reference to an absent payment: still counted
    {'query': mq('count', MB, 4), 'value': 1, 'needed': mids(5), 'relevant': mids(2, 4, 5)},
    {'query': mq('count', MA, 3), 'value': 2, 'needed': mids(2, 3), 'relevant': mids(1, 2, 3)},
])

# 5. A correction of the award (read back into the payment that is half of the award made at the first meeting) and
# a correction of a payment.
m_case('corrections', [
    mev(1, 'award', 1, MA, amount=10000),
    m_abs_pay(2, 2, MA, 2000),
    mev(3, 'pay', 3, MA, refs=(1,), spec={'type': 'frac_award', 'award_m': 1, 'frac': [1, 2], 'award': 'T0001'}),
    mev(4, 'correct', 4, MA, refs=(1,), target='T0001', target_kind='award', target_m=1, new=12000),
    m_abs_pay(5, 5, MA, 3000),
    mev(6, 'correct', 6, MA, refs=(5,), target='T0005', target_kind='pay', target_m=5, new=3500),
], [
    {'query': mq('award', MA, 4), 'value': 12000, 'needed': mids(1, 4), 'relevant': mids(1, 2, 3, 4, 5, 6)},
    {'query': mq('total', MA, 3), 'value': 8000, 'needed': mids(1, 2, 3, 4), 'relevant': mids(1, 2, 3, 4, 5, 6)},
    {'query': mq('total', MA, 6), 'value': 11500, 'needed': mids(1, 2, 3, 4, 5, 6), 'relevant': mids(1, 2, 3, 4, 5, 6)},
    {'query': mq('largest', MA, 6), 'value': 6000, 'needed': mids(1, 3, 4), 'relevant': mids(1, 2, 3, 4, 5, 6)},
    # the payment that is half of the award is counted whatever the award is: the award and its correction are not needed
    {'query': mq('count', MA, 3), 'value': 2, 'needed': mids(2, 3), 'relevant': mids(1, 2, 3, 4, 5, 6)},
])

# 6. A returned cheque: the return counts from the meeting that reports it; the payment counts until then.
m_case('returned cheque', [
    mev(1, 'award', 1, MA, amount=10000),
    m_abs_pay(2, 2, MA, 3000),
    m_abs_pay(3, 3, MA, 2000),
    mev(4, 'return', 4, MA, refs=(2,), target='T0002', target_m=2),
], [
    {'query': mq('total', MA, 4), 'value': 2000, 'needed': mids(3, 4), 'relevant': mids(1, 2, 3, 4)},
    {'query': mq('count', MA, 4), 'value': 1, 'needed': mids(3, 4), 'relevant': mids(1, 2, 3, 4)},
    {'query': mq('total', MA, 3), 'value': 5000, 'needed': mids(2, 3), 'relevant': mids(1, 2, 3, 4)},
])

# 7. A rescission, and the number of projects holding an award.
m_case('rescission', [
    mev(1, 'award', 1, MA, amount=9000),
    mev(2, 'rescind', 3, MA, refs=(1,)),
    mev(3, 'award', 1, MB, amount=5000),
], [
    {'query': mq('award', MA, 4), 'value': NONE, 'needed': mids(1, 2), 'relevant': mids(1, 2)},
    {'query': mq('award', MA, 2), 'value': 9000, 'needed': mids(1), 'relevant': mids(1, 2)},
    {'query': mq('projects', None, 4), 'value': 1, 'needed': mids(2, 3), 'relevant': mids(1, 2, 3)},
])

# 8. The balance of the award, and a payment that is "the balance of the award".
m_case('balance', [
    mev(1, 'award', 1, MA, amount=10000),
    m_abs_pay(2, 2, MA, 2500),
    mev(3, 'pay', 3, MA, refs=(1,), spec={'type': 'balance'}),
], [
    {'query': mq('balance', MA, 3), 'value': 0, 'needed': mids(1, 3), 'relevant': mids(1, 2, 3)},
    {'query': mq('total', MA, 3), 'value': 10000, 'needed': mids(1, 3), 'relevant': mids(1, 2, 3)},
    {'query': mq('balance', MA, 2), 'value': 7500, 'needed': mids(1, 2), 'relevant': mids(1, 2, 3)},
    {'query': mq('count', MA, 3), 'value': 2, 'needed': mids(2, 3), 'relevant': mids(1, 2, 3)},
])

# 9. The first meeting at which the award was fully paid.
m_case('first full payment', [
    mev(1, 'award', 1, MA, amount=10000),
    m_abs_pay(2, 2, MA, 6000),
    mev(3, 'pay', 3, MA, refs=(1,), spec={'type': 'balance'}),
    mev(4, 'award', 1, MB, amount=7000),
    m_abs_pay(5, 2, MB, 7000),
    mev(6, 'return', 3, MB, refs=(5,), target='T0005', target_m=2),
], [
    {'query': mq('first', MA), 'value': mD(3), 'needed': mids(1, 3), 'relevant': mids(1, 2, 3)},
    {'query': mq('first', MB), 'value': mD(2), 'needed': mids(4, 5), 'relevant': mids(4, 5, 6)},
])

# 10. A variation by a fraction of the award in force, with a motion that fell beside it.
m_case('variation', [
    mev(1, 'award', 1, MA, amount=10000),
    mev(2, 'vary', 2, MA, refs=(1,), sign=1, delta={'frac': [1, 2]}),
    mev(3, 'fell_vary', 3, MA, sign=-1, delta={'money': 4000}),
], [
    {'query': mq('award', MA, 3), 'value': 15000, 'needed': mids(1, 2), 'relevant': mids(1, 2, 3)},
])

# 11. A treasurer's summary that restates a payment already reported.
m_case('restatement', [
    mev(1, 'award', 1, MA, amount=10000),
    m_abs_pay(2, 2, MA, 4000),
    mev(3, 'restate', 3, MA, entries=[{'pay': 'T0002', 'amount': 4000}]),
], [
    {'query': mq('total', MA, 3), 'value': 4000, 'needed': mids(2), 'relevant': mids(1, 2, 3)},
])

# 12. A payment told after a lead paragraph, "for the undertaking named in the paragraph above": the lead paragraph is
# a naming event (kind `name`, of the project it names), the payment depends on it.
m_case('the paragraph above', [
    mev(1, 'award', 1, MA, amount=12000),
    mev(2, 'names', 2, MA),
    mev(3, 'pay', 2, MA, named_by=(2,), spec={'type': 'abs', 'amount': 3000}),
    m_abs_pay(4, 3, MA, 2000),
    mev(5, 'award', 1, MB, amount=8000),
    mev(6, 'names', 3, MB),
    mev(7, 'pay', 3, MB, named_by=(6,), spec={'type': 'abs', 'amount': 1000}),
], [
    {'query': mq('total', MA, 3), 'value': 5000, 'needed': mids(2, 3, 4), 'relevant': mids(1, 2, 3, 4)},
    {'query': mq('count', MA, 3), 'value': 2, 'needed': mids(2, 3, 4), 'relevant': mids(1, 2, 3, 4)},
    {'query': mq('total', MB, 3), 'value': 1000, 'needed': mids(6, 7), 'relevant': mids(5, 6, 7)},
])

# 13. "A sum equal to the second instalment" (an amount: counted even when it cannot be read) and "the second
# instalment was returned" (a name: the payments that fix the numbering are needed).
m_case('the second instalment', [
    mev(1, 'award', 1, MA, amount=30000),
    m_abs_pay(2, 2, MA, 4000),
    m_abs_pay(3, 3, MA, 1500),
    mev(4, 'pay', 4, MA, refs=(3,), amount_by=(2,), spec={'type': 'ordinal', 'k': 2, 'of': 'T0003'}),
    mev(5, 'return', 5, MA, refs=(3,), named_by=(2,), target='T0003', target_m=3),
], [
    {'query': mq('total', MA, 4), 'value': 7000, 'needed': mids(2, 3, 4), 'relevant': mids(1, 2, 3, 4, 5)},
    {'query': mq('count', MA, 4), 'value': 3, 'needed': mids(2, 3, 4), 'relevant': mids(1, 2, 3, 4, 5)},
    {'query': mq('total', MA, 5), 'value': 5500, 'needed': mids(2, 3, 4, 5), 'relevant': mids(1, 2, 3, 4, 5)},
])


# ---------------------------------------------------------------------------------------------------------
# Hand-written cases of the succession family (14 cases). Cases 1 to 11 were written from the reading rules in the
# procedure text of evalsuite/big_succession.py, with the value, the needed set (removal from the full list changes the
# value) and the relevant set, and the value of a few subsets, written down BEFORE the interpreter was run on them.
# Cases 12 to 14 (first held, changes of holder, part of) were written, also before they were run, AFTER the code of
# `present`, `answer_in` and `do_join` in the module had been read: they are less independent of it. One slip in case
# 14's subsets was corrected before running. Helpers and names carry the prefix `s_` so that they do not meet those above. A fact
# told in a paragraph that names a party or a berth through a description lists the passage behind it in `needs`; a
# register note and a correction list what they are about in `ref`. Subsets apply to the first query of a case; an
# answer on a subset may be a Fraction, which is compared and never serialised.
# ---------------------------------------------------------------------------------------------------------

S_PEOPLE = {'P1': 'Clara Hartley', 'P2': 'Ulick Cheswick', 'P3': 'Mara Pell', 'P4': 'Halyard & Sons', 'P5': 'Fenn Boats',
          'P6': 'Anchor Group', 'P7': 'Quay Chandlers Ltd', 'P8': 'Marlow Stores'}


def s_ev(i, kind, date, needs=(), ref=None, **fields):
    out = {'id': 'E%03d' % i, 'kind': kind, 'date': date, 'when': date, 'seq': i, 'needs': ['E%03d' % n for n in needs],
           'decides': kind != 'decoy'}
    if ref is not None:
        out['ref'] = 'E%03d' % ref
    out.update(fields)
    names = {}
    for key in ('party', 'assignor', 'assignee', 'deceased', 'executor', 'heir', 'buyer', 'seller', 'person', 'anchor',
                'wrong', 'value'):
        v = out.get(key)
        if isinstance(v, str) and v in S_PEOPLE and (key != 'value' or out.get('field') == 'assignee'):
            names[v] = S_PEOPLE[v]
    for p in out.get('merged', ()):
        names[p] = S_PEOPLE[p]
    out['party_names'] = names
    return out


def s_grant(i, date, berth, label, party):
    return s_ev(i, 'grant', date, berth=berth, label=label, party=party, new=True)


def s_fee(i, date, berth, label, year, spec, needs=()):
    return s_ev(i, 'fee', date, needs=needs, berth=berth, label=label, year=year, spec=spec)


def s_agree(i, date, berth, label, assignor, assignee, needs=()):
    return s_ev(i, 'agree', date, needs=needs, berth=berth, label=label, assignor=assignor, assignee=assignee)


def s_register(i, date, berth, label, ref):
    return s_ev(i, 'register', date, ref=ref, berth=berth, label=label)


def s_holder(berth, label, date):
    return {'kind': 'holder', 'berth': berth, 'label': label, 'date': date}


def s_feeq(berth, label, year):
    return {'kind': 'fee', 'berth': berth, 'label': label, 'year': year}


def s_count(party, date):
    return {'kind': 'count', 'party': party, 'date': date}


def s_ids(*numbers):
    return {'E%03d' % n for n in numbers}


def s_abs_fee(amount):
    return {'type': 'abs', 'amount': amount}


SUCCESSION_CASES = []


def s_case(name, events, queries, subsets=()):
    SUCCESSION_CASES.append({'name': name, 'events': events, 'queries': queries, 'subsets': list(subsets)})


# 1. A berth next to another: B01 takes the fee of the berth it is paired with. The grant of the other berth is not
# needed for B01's fee (its fee entry is there without it); the pairing and the other berth's entry are. The decoy is
# an event of the berth (it decides nothing), so it is relevant and not needed. (Corrected before the first run: the
# first draft left the decoy out of the relevant set.)
s_case('a berth next to another', [
    s_grant(1, '2016-01-10', 'B01', '10', 'P1'),
    s_grant(2, '2016-01-20', 'B02', '11', 'P2'),
    s_ev(3, 'pair', '2016-02-01', berths=['B01', 'B02'], labels=['10', '11']),
    s_fee(4, '2016-03-01', 'B02', '11', 2016, s_abs_fee(1000)),
    s_fee(5, '2016-12-01', 'B01', '10', 2017, {'type': 'ref', 'target': 'adjoin', 'other': None, 'other_label': None,
                                             'num': 1, 'den': 1}),
    s_ev(6, 'decoy', '2016-05-01', berth='B01', label='10', party='P1'),
], [
    {'query': s_feeq('B01', '10', 2017), 'value': 1000, 'needed': s_ids(1, 3, 4, 5), 'relevant': s_ids(1, 3, 4, 5, 6)},
], [([1, 2, 4, 5], 'none'), ([1, 3, 4, 5], 1000)])

# 2. A party named by kinship: P2 is "the only son of Mr Hale" (a kin passage); the agreement names him so, and the
# register note enters it. A date before the entry in the register has the assignor as holder.
s_case('a party named by kinship', [
    s_grant(1, '2016-01-10', 'B01', '10', 'P1'),
    s_ev(2, 'kin', '2016-02-01', person='P2', anchor='P1', relation='son'),
    s_agree(3, '2016-03-01', 'B01', '10', 'P1', 'P2', needs=(2,)),
    s_register(4, '2016-04-01', 'B01', '10', 3),
], [
    {'query': s_holder('B01', '10', '2016-05-01'), 'value': 'Ulick Cheswick', 'needed': s_ids(1, 2, 3, 4),
     'relevant': s_ids(1, 2, 3, 4)},
    {'query': s_holder('B01', '10', '2016-03-15'), 'value': 'Clara Hartley', 'needed': s_ids(1), 'relevant': s_ids(1, 2, 3, 4)},
], [([1, 3, 4], 'Clara Hartley'), ([1, 2, 3], 'Clara Hartley')])

# 3. A party named by a new name: P4 was renamed on 1 January 2017 and the agreement names it by the new name.
s_case('a party named by a new name', [
    s_grant(1, '2016-01-10', 'B01', '10', 'P1'),
    s_grant(2, '2016-01-15', 'B02', '20', 'P4'),
    s_ev(3, 'rename', '2016-12-20', party='P4', name='Tern Marine', when='2017-01-01'),
    s_agree(4, '2017-03-01', 'B01', '10', 'P1', 'P4', needs=(3,)),
    s_register(5, '2017-04-01', 'B01', '10', 4),
], [
    {'query': s_holder('B01', '10', '2017-06-01'), 'value': 'Halyard & Sons', 'needed': s_ids(1, 3, 4, 5),
     'relevant': s_ids(1, 3, 4, 5)},
], [([1, 4, 5], 'Clara Hartley')])

# 4. A merged firm: the merged firm is a new holder and holds what either firm held.
s_case('a merged firm', [
    s_grant(1, '2016-01-10', 'B01', '10', 'P4'),
    s_grant(2, '2016-01-12', 'B02', '11', 'P5'),
    s_ev(3, 'merge', '2017-02-01', merged=['P4', 'P5'], party='P6', name='Anchor Group'),
], [
    {'query': s_count('P6', '2017-03-01'), 'value': 2, 'needed': s_ids(1, 2, 3), 'relevant': s_ids(1, 2, 3)},
    {'query': s_holder('B01', '10', '2017-03-01'), 'value': 'Anchor Group', 'needed': s_ids(1, 3), 'relevant': s_ids(1, 3)},
    {'query': s_holder('B01', '10', '2016-12-01'), 'value': 'Halyard & Sons', 'needed': s_ids(1), 'relevant': s_ids(1, 3)},
])

# 5. A buyer of a business: the sale passes no licence; the agreement names the assignee as the buyer.
s_case('the buyer of a business', [
    s_grant(1, '2016-01-10', 'B01', '10', 'P7'),
    s_ev(2, 'bought', '2016-02-01', seller='P7', buyer='P8', trade='chandlery'),
    s_agree(3, '2016-03-01', 'B01', '10', 'P7', 'P8', needs=(2,)),
    s_register(4, '2016-04-01', 'B01', '10', 3),
], [
    {'query': s_holder('B01', '10', '2016-05-01'), 'value': 'Marlow Stores', 'needed': s_ids(1, 2, 3, 4),
     'relevant': s_ids(1, 2, 3, 4)},
    {'query': s_holder('B01', '10', '2016-02-15'), 'value': 'Quay Chandlers Ltd', 'needed': s_ids(1),
     'relevant': s_ids(1, 2, 3, 4)},
])

# 6. "The holder of berth 12", where berth 12 was number 11 until a renumbering: the passages behind the description (the
# renumbering and the grant that made P2 its holder) are what the agreement leans on.
s_case('the holder of berth N, N a later number', [
    s_grant(1, '2016-01-10', 'B01', '10', 'P1'),
    s_grant(2, '2016-01-15', 'B02', '11', 'P2'),
    s_ev(3, 'renumber', '2016-06-01', berth='B02', old='11', label='12'),
    s_agree(4, '2016-07-01', 'B01', '10', 'P1', 'P2', needs=(3, 2)),
    s_register(5, '2016-08-01', 'B01', '10', 4),
], [
    {'query': s_holder('B01', '10', '2016-09-01'), 'value': 'Ulick Cheswick', 'needed': s_ids(1, 2, 3, 4, 5),
     'relevant': s_ids(1, 2, 3, 4, 5)},
], [([1, 2, 4, 5], 'Clara Hartley'), ([1, 3, 4, 5], 'Clara Hartley')])

# 7. A registration without its agreement, and an agreement never registered.
s_case('a registration without its agreement', [
    s_grant(1, '2016-01-10', 'B01', '10', 'P1'),
    s_agree(2, '2016-03-01', 'B01', '10', 'P1', 'P2'),
    s_register(3, '2016-04-01', 'B01', '10', 2),
], [
    {'query': s_holder('B01', '10', '2016-05-01'), 'value': 'Ulick Cheswick', 'needed': s_ids(1, 2, 3),
     'relevant': s_ids(1, 2, 3)},
    {'query': s_holder('B01', '10', '2016-03-20'), 'value': 'Clara Hartley', 'needed': s_ids(1), 'relevant': s_ids(1, 2, 3)},
], [([1, 3], 'Clara Hartley'), ([1, 2], 'Clara Hartley')])

# 8. A correction: the agreement was written with P2 as assignee and corrected to P3 later; the correction applies as if
# the entry had always read so.
s_case('a correction', [
    s_grant(1, '2016-01-10', 'B01', '10', 'P1'),
    s_agree(2, '2016-03-01', 'B01', '10', 'P1', 'P2'),
    s_register(3, '2016-04-01', 'B01', '10', 2),
    s_ev(4, 'fix', '2016-09-01', ref=2, field='assignee', value='P3', berth='B01', label='10', party='P3', wrong='P2'),
], [
    {'query': s_holder('B01', '10', '2016-10-01'), 'value': 'Mara Pell', 'needed': s_ids(1, 2, 3, 4),
     'relevant': s_ids(1, 2, 3, 4)},
    {'query': s_holder('B01', '10', '2016-05-01'), 'value': 'Mara Pell', 'needed': s_ids(1, 2, 3, 4),
     'relevant': s_ids(1, 2, 3, 4)},
], [([1, 2, 3], 'Ulick Cheswick')])

# 9. A relative fee without its base.
s_case('a relative fee without its base', [
    s_grant(1, '2016-01-10', 'B01', '10', 'P1'),
    s_fee(2, '2016-02-01', 'B01', '10', 2016, s_abs_fee(1000)),
    s_fee(3, '2016-12-01', 'B01', '10', 2017, {'type': 'rel', 'num': 1, 'den': 10}),
], [
    {'query': s_feeq('B01', '10', 2017), 'value': 1100, 'needed': s_ids(1, 2, 3), 'relevant': s_ids(1, 2, 3)},
    {'query': s_feeq('B01', '10', 2016), 'value': 1000, 'needed': s_ids(1, 2), 'relevant': s_ids(1, 2, 3)},
], [([1, 3], 'none'), ([1, 2], 1000)])

# 10. A fee correction whose absence passes through a sum that is not whole: the entry was written as 1,005 and corrected
# to 1,000; the next entry raises the fee by a tenth. Without the correction the sum is 1,105.5.
s_case('a fee correction through a non-whole sum', [
    s_grant(1, '2016-01-10', 'B01', '10', 'P1'),
    s_fee(2, '2016-02-01', 'B01', '10', 2016, s_abs_fee(1005)),
    s_fee(3, '2016-12-01', 'B01', '10', 2017, {'type': 'rel', 'num': 1, 'den': 10}),
    s_ev(4, 'fix', '2017-02-01', ref=2, field='amount', value=1000, berth='B01', label='10'),
], [
    {'query': s_feeq('B01', '10', 2017), 'value': 1100, 'needed': s_ids(1, 2, 3, 4), 'relevant': s_ids(1, 2, 3, 4)},
], [([1, 2, 3], Fraction(2211, 2)), ([1, 2, 4], 1000)])

# 11. A count of berths held: P1 holds B01 and B02; B02 is surrendered on 1 January 2017.
s_case('a count of berths held', [
    s_grant(1, '2016-01-10', 'B01', '10', 'P1'),
    s_grant(2, '2016-01-15', 'B02', '11', 'P1'),
    s_grant(3, '2016-02-01', 'B03', '12', 'P2'),
    s_ev(4, 'surrender', '2017-01-01', berth='B02', label='11'),
], [
    {'query': s_count('P1', '2016-12-01'), 'value': 2, 'needed': s_ids(1, 2), 'relevant': s_ids(1, 2, 3, 4)},
    {'query': s_count('P1', '2017-02-01'), 'value': 1, 'needed': s_ids(1, 4), 'relevant': s_ids(1, 2, 3, 4)},
])


# Cases 12 to 14 were added later, before any run on them, for the three kinds of statement that cases 1 to 11 do not
# reach (first held, changes of holder, part of). Written from the same reading rules.

def s_firstq(berth, label, party):
    return {'kind': 'first_held', 'berth': berth, 'label': label, 'party': party}


def s_changesq(berth, label, d1, d2):
    return {'kind': 'changes', 'berth': berth, 'label': label, 'from': d1, 'to': d2}


def s_partq(berth, label):
    return {'kind': 'part', 'berth': berth, 'label': label}


# 12. When a party first held a berth: the date its registration took effect, not the date of the agreement. The grant
# gives its holder the berth from its own date.
s_case('when a party first held a berth', [
    s_grant(1, '2016-01-10', 'B01', '10', 'P1'),
    s_agree(2, '2016-03-01', 'B01', '10', 'P1', 'P2'),
    s_register(3, '2016-04-01', 'B01', '10', 2),
], [
    {'query': s_firstq('B01', '10', 'P2'), 'value': '2016-04-01', 'needed': s_ids(1, 2, 3), 'relevant': s_ids(1, 2, 3)},
    {'query': s_firstq('B01', '10', 'P1'), 'value': '2016-01-10', 'needed': s_ids(1), 'relevant': s_ids(1, 2, 3)},
], [([1, 2], NONE), ([1, 2, 3], '2016-04-01')])

# 13. Changes of the holder between two dates: two registered transfers inside the window make two changes.
s_case('changes of the holder of a berth', [
    s_grant(1, '2016-01-10', 'B01', '10', 'P1'),
    s_agree(2, '2016-03-01', 'B01', '10', 'P1', 'P2'),
    s_register(3, '2016-04-01', 'B01', '10', 2),
    s_agree(4, '2016-06-01', 'B01', '10', 'P2', 'P3'),
    s_register(5, '2016-07-01', 'B01', '10', 4),
], [
    {'query': s_changesq('B01', '10', '2016-02-01', '2016-12-31'), 'value': 2, 'needed': s_ids(1, 2, 3, 4, 5),
     'relevant': s_ids(1, 2, 3, 4, 5)},
])

# 14. A berth that forms part of another: B02 was joined to B01 on 1 January 2017, which continues as number 10. The join
# itself establishes the berth that continues (the reading note of `present`), so the grant of B01 is not needed.
s_case('a berth that forms part of another', [
    s_grant(1, '2016-01-10', 'B01', '10', 'P1'),
    s_grant(2, '2016-01-15', 'B02', '11', 'P1'),
    s_ev(3, 'join', '2017-01-01', survivor='B01', label='10', berths=['B01', 'B02'], labels=['10', '11'], holder='P1'),
], [
    {'query': s_partq('B02', '11'), 'value': '10', 'needed': s_ids(2, 3), 'relevant': s_ids(1, 2, 3)},
], [([2, 3], '10'), ([1, 3], None)])      # without the grant of B02 the berth is not established: None


def item_event(item, event_id):
    return next(e for e in item['events'] if e['id'] == event_id)


def hand_label(item, query):
    return item['name'], query['query'].get('date') or query['query']['kind'], query['query'].get('category')


class HandCaseTests(unittest.TestCase):
    """The only tests whose expected results do not come from the code under test."""

    def check(self, item, query):
        module = importlib.import_module('big_amendments')
        label = hand_label(item, query)
        got = check_shown.analyse(module, query['query'], item['events'])
        self.assertEqual(got['true'], query['value'], label)
        # the hand-written needed sets are written by the definition of removal; the checker's `needed` is those closed
        # under `name` dependencies, which holds the same events or more
        self.assertEqual(set(got['needed_by_removal']), query['needed'], label)
        self.assertTrue(set(got['needed_by_removal']) <= set(got['needed']), label)
        self.assertEqual(got['relevant'], query['relevant'], label)
        # the needed events are among the relevant ones, and the interpreter and the checker agree on the value
        self.assertTrue(set(got['needed']) <= got['relevant'], label)
        self.assertEqual(module.answer(query['query'], item['events']), query['value'], label)
        # no needed event is one whose removal only makes the value "not established", except the parameter's own
        # clause (spec 3.3: None only when what the statement is about cannot be computed at all)
        for event in got['needed_by_removal']:
            if item_event(item, event)['kind'] == 'parameter' and item_event(item, event)['parameter'] == query[
                    'query'].get('parameter'):
                continue
            rest = [e for e in item['events'] if e['id'] != event]
            self.assertIsNotNone(module.answer(query['query'], rest), (label, event))

    def test_the_hand_written_cases(self):
        count = 0
        for item in HAND_CASES:
            for query in item['queries']:
                with self.subTest(hand_label(item, query)):
                    self.check(item, query)
                count += 1
        self.assertGreaterEqual(count, 20)
        self.assertGreaterEqual(len(HAND_CASES), 10)

    def test_every_event_of_a_hand_case_is_a_fact_the_module_can_read(self):
        module = importlib.import_module('big_amendments')
        for item in HAND_CASES:
            ids = [event['id'] for event in item['events']]
            self.assertEqual(len(set(ids)), len(ids), item['name'])
            for event in item['events']:
                self.assertTrue(set(event.get('requires', ())) <= set(ids), (item['name'], event['id']))
            self.assertTrue(all(module.answer(q['query'], item['events']) is not None for q in item['queries']),
                            item['name'])


# ---------------------------------------------------------------------------------------------------------
# Units: reading a value, locating a quote
# ---------------------------------------------------------------------------------------------------------

class RedundantProvisionTests(unittest.TestCase):
    """Hand case 'count of instruments': two provisions of one instrument either of which gives the count. Neither is
    needed (removing one changes nothing), so a basis that cites neither must still fail: sufficient means the cited
    events contain every needed event AND give the true value (spec 3.3, step 4)."""

    def setUp(self):
        module = importlib.import_module('big_amendments')
        item = next(c for c in HAND_CASES if c['name'] == 'count of instruments')
        self.module, self.events = module, item['events']
        words = ['alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima',
                 'mike november oscar papa quebec romeo sierra tango uniform victor whiskey xray',
                 'yankee zulu amber basalt cobalt dahlia ember fennel garnet heather indigo jasper',
                 'kestrel lantern meadow nectar orchid pewter quartz russet saffron thistle umber velvet',
                 'walnut yarrow zephyr acorn birch cedar dune elm fig gorse hazel iris']
        self.texts = {'a.md': '\n\n'.join(words) + '\n'}
        self.sources = check_shown.Sources(self.texts)
        self.blocks = check_shown.split_paragraphs(self.texts['a.md'])
        passages = [{'file': 'a.md', 'paragraph': n, 'event_ids': [event['id']], 'role': 'states'}
                    for n, event in enumerate(self.events)]
        self.family = check_shown.Family(module, {'events': self.events, 'passages': passages, 'names': {}},
                                         'amendments')
        self.queries = {q['value']: q for q in next(c for c in HAND_CASES if c['name'] == 'count of instruments')['queries']}

    def judge(self, query, events):
        row = {'claim_id': 'C01', 'variant': 'correct', 'semantic_state': 'supported', 'claim_kind': 'count',
               'query': query['query'], 'value_form': 'integer',
               'derivation': {'stated': query['value'], 'worlds': 1}}
        quotes = [{'quote': self.blocks[int(event[1:]) - 1]} for event in events]
        sheet = {'status': 'valid', 'rows': [{'claim_id': 'C01', 'semantic_state': 'supported',
                                             'value': query['value'], 'evidence': quotes}]}
        return check_shown.judge_review(self.family, [row], sheet, self.sources, 'answered')[0]

    def test_citing_one_of_the_two_provisions_shows_it_and_citing_neither_does_not(self):
        query = self.queries[1]            # the count on 2031-04-01: E002 or E003, both of one instrument
        for events in (['E001', 'E002'], ['E001', 'E003'], ['E001', 'E002', 'E003']):
            got = self.judge(query, events)
            self.assertEqual((got['class'], got['kind']), ('shown', 'none'), events)
            self.assertTrue(got['basis']['cited_answer_is_true'])
        got = self.judge(query, ['E001'])
        self.assertEqual((got['class'], got['kind']), ('right_not_shown', 'reading'))
        self.assertEqual(got['basis']['missing'], [])                 # every needed event is cited ...
        self.assertFalse(got['basis']['cited_answer_is_true'])        # ... and the cited events do not give the count
        self.assertFalse(got['basis']['sufficient'])
        self.assertIn('do not give the true value', got['detail'])
        got = self.judge(query, ['E002', 'E003'])
        self.assertEqual((got['class'], got['kind']), ('right_not_shown', 'reading'))
        self.assertEqual(got['basis']['missing'], ['E001'])

    def test_the_same_when_the_count_has_two_instruments(self):
        query = self.queries[2]            # the count on 2031-08-01: needed {E001, E004}; one of E002 / E003 is also due
        self.assertEqual(self.judge(query, ['E001', 'E004', 'E002'])['class'], 'shown')
        self.assertEqual(self.judge(query, ['E001', 'E004', 'E003'])['class'], 'shown')
        got = self.judge(query, ['E001', 'E004'])
        self.assertEqual((got['class'], got['kind'], got['basis']['missing']), ('right_not_shown', 'reading', []))

    def test_an_unlocated_quote_makes_the_failure_one_of_format(self):
        query = self.queries[1]
        row = {'claim_id': 'C01', 'variant': 'correct', 'semantic_state': 'supported', 'claim_kind': 'count',
               'query': query['query'], 'value_form': 'integer', 'derivation': {'stated': 1, 'worlds': 1}}
        sheet = {'status': 'valid', 'rows': [{'claim_id': 'C01', 'semantic_state': 'supported', 'value': 1, 'evidence': [
            {'quote': self.blocks[0]}, {'quote': 'a made up sentence of more than forty characters in all'}]}]}
        got = check_shown.judge_review(self.family, [row], sheet, self.sources, 'answered')[0]
        self.assertEqual((got['class'], got['kind']), ('right_not_shown', 'format'))


class MinutesHandCaseTests(unittest.TestCase):
    """The only tests of the minutes family whose expected results do not come from the code under test."""

    def label(self, item, query):
        return item['name'], query['query']['kind'], query['query']['until'], query['query']['subject']

    def queries(self):
        for item in MINUTES_CASES:
            for query in item['queries']:
                yield item, query

    def check(self, item, query):
        module = importlib.import_module('big_minutes')
        got = check_shown.analyse(module, query['query'], item['events'])
        self.assertEqual(got['true'], query['value'])
        self.assertEqual(set(got['needed_by_removal']), query['needed'])
        self.assertEqual(got['relevant'], query['relevant'])
        self.assertTrue(set(got['needed']) <= got['relevant'])
        # removing a needed event never only makes the value "not established", unless it is the project's own award
        # (spec 3.3, subset rule)
        for event in got['needed_by_removal']:
            rest = [e for e in item['events'] if e['id'] != event]
            if module.answer(query['query'], rest) is None:
                gone = next(e for e in item['events'] if e['id'] == event)
                self.assertEqual((gone['kind'], gone['subject']), ('award', query['query']['subject']))

    def test_the_hand_written_cases(self):
        count = 0
        for item, query in self.queries():
            with self.subTest(self.label(item, query)):
                self.check(item, query)
            count += 1
        self.assertGreaterEqual(count, 35)
        self.assertGreaterEqual(len(MINUTES_CASES), 12)

    def test_every_kind_of_statement_and_event_of_the_family_is_in_a_hand_case(self):
        kinds = {query['query']['kind'] for item in MINUTES_CASES for query in item['queries']}
        self.assertEqual(kinds, {'total', 'award', 'balance', 'count', 'projects', 'first', 'largest'})
        events = {e['kind'] for item in MINUTES_CASES for e in item['events']}
        self.assertEqual(events, {'intro', 'award', 'defer_award', 'vary', 'fell_vary', 'correct', 'rescind',
                                  'authorise', 'pay', 'return', 'restate', 'outside', 'names'})
        specs = {e['spec']['type'] for item in MINUTES_CASES for e in item['events'] if e['kind'] == 'pay'}
        self.assertEqual(specs, {'abs', 'first', 'first_of', 'frac_award', 'authorised', 'balance', 'ordinal'})

    def mini(self, case_name, number):
        """The returned-cheque case as a dossier of its own: one paragraph for each event."""
        module = importlib.import_module('big_minutes')
        item = next(c for c in MINUTES_CASES if c['name'] == case_name)
        words = ['alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike',
                 'november oscar papa quebec romeo sierra tango uniform victor whiskey xray yankee',
                 'zulu amber basalt cobalt dahlia ember fennel garnet heather indigo jasper kestrel',
                 'lantern meadow nectar orchid pewter quartz russet saffron thistle umber velvet walnut',
                 'yarrow zephyr acorn birch cedar dune elm fig gorse hazel iris juniper laurel maple']
        sources = check_shown.Sources({'book.md': '\n\n'.join(words) + '\n'})
        blocks = check_shown.split_paragraphs('\n\n'.join(words) + '\n')
        passages = [{'file': 'book.md', 'paragraph': n, 'event_ids': [e['id']], 'role': 'states'}
                    for n, e in enumerate(item['events'])]
        family = check_shown.Family(module, {'events': item['events'], 'passages': passages, 'names': {}},
                                    'minutes')
        query = item['queries'][number]
        row = {'claim_id': 'C01', 'variant': 'correct', 'semantic_state': 'supported', 'claim_kind': 'minutes_total',
               'query': query['query'], 'value_form': 'integer', 'derivation': {'stated': query['value'], 'worlds': 1}}

        def judge(events):
            sheet = {'status': 'valid', 'rows': [{'claim_id': 'C01', 'semantic_state': 'supported',
                                                 'value': query['value'], 'evidence': [
                                                     {'quote': blocks[int(e[1:]) - 1]} for e in events]}]}
            return check_shown.judge_review(family, [row], sheet, sources, 'answered')[0]
        return judge

    def test_a_returned_cheque_is_shown_by_the_payment_that_counts_and_the_return(self):
        judge = self.mini('returned cheque', 0)       # the total after the return: 2,000; needed T0003 and T0004
        self.assertEqual(judge(['T0003', 'T0004'])['class'], 'shown')
        self.assertEqual(judge(['T0002', 'T0003', 'T0004'])['class'], 'shown')      # the returned payment: on the subject
        got = judge(['T0003'])        # T0004, the return, is needed (without it the payment returned would count)
        self.assertEqual((got['class'], got['kind'], got['basis']['missing']), ('right_not_shown', 'reading', ['T0004']))
        self.assertTrue(got['basis']['cited_answer_is_true'])         # the one payment alone gives 2,000, but not the basis
        got = judge(['T0002', 'T0003'])               # the payments without the return: 5,000 is not 2,000
        self.assertEqual((got['class'], got['basis']['missing']), ('right_not_shown', ['T0004']))

    def test_a_payment_that_is_the_balance_of_the_award_is_shown_without_the_payment_it_makes_redundant(self):
        judge = self.mini('balance', 1)               # the total of 10,000: needed T0001 (award), T0003 (balance)
        self.assertEqual(judge(['T0001', 'T0003'])['class'], 'shown')
        self.assertEqual(judge(['T0003'])['basis']['missing'], ['T0001'])
        self.assertEqual(judge(['T0001', 'T0002'])['class'], 'right_not_shown')


class SuccessionHandCaseTests(unittest.TestCase):
    """The only tests of the succession family whose expected results do not come from the code under test."""

    def module(self):
        return importlib.import_module('big_succession')

    def label(self, item, query):
        return item['name'], query['query']['kind']

    def queries(self):
        for item in SUCCESSION_CASES:
            for query in item['queries']:
                yield item, query

    def test_the_hand_written_cases(self):
        module, count = self.module(), 0
        for item, query in self.queries():
            with self.subTest(self.label(item, query)):
                got = check_shown.analyse(module, query['query'], item['events'])
                self.assertEqual(got['true'], query['value'])
                self.assertEqual(set(got['needed_by_removal']), query['needed'])
                self.assertTrue(set(got['needed_by_removal']) <= set(got['needed']))
                self.assertEqual(got['relevant'], query['relevant'])
                self.assertTrue(set(got['needed']) <= got['relevant'])
                self.assertEqual(module.answer(query['query'], item['events']), query['value'])
                # removing a needed event may only make the value "not established" where the event is about the berth or
                # the party of the statement (or what such an event cannot be read without)
                for event in got['needed_by_removal']:
                    rest = [e for e in item['events'] if e['id'] != event]
                    if module.answer(query['query'], rest) is None:
                        self.assertIn(event, about_succession(item['events'], query['query'], module),
                                      (self.label(item, query), event))
            count += 1
        self.assertEqual(count, 23)
        self.assertEqual(len(SUCCESSION_CASES), 14)

    def test_the_value_of_the_hand_written_subsets(self):
        """What a reading of part of the events gives is written by hand too. It may be a Fraction (a sum that is not
        whole, case 10): it is compared and never formatted or written out."""
        module, seen, fractions = self.module(), 0, 0
        for item in SUCCESSION_CASES:
            query = item['queries'][0]['query']
            for numbers, want in item['subsets']:
                chosen = [e for e in item['events'] if int(e['id'][1:]) in numbers]
                got = module.answer(query, chosen)
                with self.subTest((item['name'], numbers)):
                    self.assertEqual(got, want)
                    self.assertIs(type(got), type(want))
                fractions += isinstance(got, Fraction)
                seen += 1
        self.assertEqual(seen, 18)
        self.assertEqual(fractions, 1)

    def test_every_kind_of_statement_of_the_family_is_in_a_hand_case(self):
        kinds = {query['query']['kind'] for item, query in self.queries()}
        self.assertEqual(kinds, {'holder', 'fee', 'count', 'first_held', 'changes', 'part'})
        events = {e['kind'] for item in SUCCESSION_CASES for e in item['events']}
        self.assertTrue({'grant', 'fee', 'pair', 'agree', 'register', 'rename', 'merge', 'bought', 'kin', 'renumber',
                         'fix', 'surrender', 'join', 'decoy'} <= events, events)

    def test_a_subset_that_gives_a_fraction_is_judged_without_writing_the_fraction_out(self):
        """Case 10 with the events the sum needs but not the correction: the cited events give 2211/2, the checker says
        so by `cited_answer_is_true` (a boolean) and the row it returns is plain JSON."""
        module = self.module()
        item = next(c for c in SUCCESSION_CASES if c['name'] == 'a fee correction through a non-whole sum')
        words = ['alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike',
                 'november oscar papa quebec romeo sierra tango uniform victor whiskey xray yankee',
                 'zulu amber basalt cobalt dahlia ember fennel garnet heather indigo jasper kestrel',
                 'lantern meadow nectar orchid pewter quartz russet saffron thistle umber velvet walnut']
        sources = check_shown.Sources({'book.md': '\n\n'.join(words) + '\n'})
        blocks = check_shown.split_paragraphs('\n\n'.join(words) + '\n')
        passages = [{'file': 'book.md', 'paragraph': n, 'event_ids': [e['id']], 'role': 'states'}
                    for n, e in enumerate(item['events'])]
        family = check_shown.Family(module, {'events': item['events'], 'passages': passages, 'names': {}}, 'succession')
        query = item['queries'][0]
        row = {'claim_id': 'C01', 'variant': 'correct', 'semantic_state': 'supported', 'claim_kind': 'fee',
               'query': query['query'], 'value_form': 'integer', 'derivation': {'stated': query['value'], 'worlds': 1}}

        def judge(events):
            sheet = {'status': 'valid', 'rows': [{'claim_id': 'C01', 'semantic_state': 'supported',
                                                 'value': query['value'], 'evidence': [
                                                     {'quote': blocks[int(e[1:]) - 1]} for e in events]}]}
            return check_shown.judge_review(family, [row], sheet, sources, 'answered')[0]
        got = judge(['E001', 'E002', 'E003'])
        self.assertIsInstance(module.answer(query['query'], family.events_of({'E001', 'E002', 'E003'})), Fraction)
        self.assertEqual((got['class'], got['kind'], got['basis']['missing']), ('right_not_shown', 'reading', ['E004']))
        self.assertFalse(got['basis']['cited_answer_is_true'])
        json.dumps(got)
        got = judge(['E001', 'E002', 'E003', 'E004'])
        self.assertEqual((got['class'], got['kind']), ('shown', 'none'))


class DecoyPassageTests(unittest.TestCase):
    """The role `decoy_passage` of the succession family, pinned. A paragraph with that role tells events of the kind
    `decoy`, which decide nothing, so a quote of it earns nothing that decides: it can never be a needed event and
    never helps a basis. It counts as on the subject (it is not an off-subject quote) exactly where `relevant` holds
    one of its events. Its passage entries carry one key more than the others, `resolves`, which the checker ignores."""

    @classmethod
    def setUpClass(cls):
        cls.b = Built.of('succession', 1)
        cls.decoys = [p for p in cls.b.passages if p['role'] == 'decoy_passage']

    def test_a_decoy_paragraph_lists_only_events_that_decide_nothing(self):
        b = self.b
        self.assertTrue(self.decoys)
        for passage in self.decoys:
            self.assertTrue(passage['event_ids'])
            for event in passage['event_ids']:
                self.assertEqual((b.by_id[event]['kind'], b.by_id[event]['decides']), ('decoy', False), passage)
        # the same paragraph is never also a paragraph that states an event that decides something
        spots = {(p['file'], p['paragraph']) for p in self.decoys}
        for passage in b.passages:
            if (passage['file'], passage['paragraph']) in spots:
                self.assertEqual(passage['role'], 'decoy_passage', passage)

    def test_no_decoy_event_is_needed_by_any_statement_and_a_decoy_paragraph_earns_only_decoys(self):
        b = self.b
        decoy_events = {e['id'] for e in b.events if e['kind'] == 'decoy'}
        for row in b.rows:
            self.assertFalse(decoy_events & set(b.needed(row)), row['claim_id'])
            self.assertFalse(decoy_events & set(b.needed_by_removal(row)), row['claim_id'])
        for passage in self.decoys:
            self.assertTrue(b.family_obj.earns[(passage['file'], passage['paragraph'])] <= decoy_events)

    def test_the_extra_key_is_ignored_by_the_checker(self):
        b = self.b
        self.assertTrue(all(isinstance(p.get('resolves'), list) for p in self.decoys))
        self.assertTrue(any('resolves' in p for p in b.passages if p['role'] != 'decoy_passage'))
        bare = [{k: v for k, v in p.items() if k != 'resolves'} for p in b.passages]
        other = check_shown.Family(b.module, dict(b.facts, passages=bare), 'succession')
        self.assertEqual(dict(other.earns), dict(b.family_obj.earns))
        self.assertEqual(dict(other.about), dict(b.family_obj.about))

    def judge(self, row, spots):
        b = self.b
        sheet = {'status': 'valid', 'rows': [{'claim_id': row['claim_id'], 'semantic_state': row['semantic_state'],
                                             'value': render(b, row, b.true(row)),
                                             'evidence': [{'quote': b.original[f][n]} for f, n in spots]}]}
        return check_shown.judge_review(b.family_obj, [row], sheet, b.sources, 'answered')[0]

    def test_a_quote_of_a_decoy_paragraph_counts_off_the_subject_unless_relevant_holds_its_event(self):
        b, seen = self.b, Counter()
        spots = [(p['file'], p['paragraph']) for p in self.decoys if b.quote((p['file'], p['paragraph']))]
        for row in b.rows[:24]:
            if not b.checkable(row):
                continue
            base, relevant = b.basis(row), b.relevant(row)
            before = self.judge(row, base)
            for spot in spots[:14]:
                got = self.judge(row, base + [spot])
                on = bool(b.about[spot] & relevant)
                self.assertEqual(got['basis']['off_subject'], before['basis']['off_subject'] + (0 if on else 1),
                                 (row['claim_id'], spot))
                self.assertEqual(got['basis']['missing'], before['basis']['missing'])      # earns nothing that decides
                self.assertEqual(got['basis']['sufficient'], before['basis']['sufficient'])
                seen['on the subject' if on else 'off the subject'] += 1
        self.assertGreater(seen['on the subject'], 0, dict(seen))
        self.assertGreater(seen['off the subject'], 0, dict(seen))

    def test_decoy_paragraphs_alone_never_show_a_statement_and_too_many_of_them_overquote(self):
        b, hit = self.b, 0
        spots = [(p['file'], p['paragraph']) for p in self.decoys if b.quote((p['file'], p['paragraph']))]
        for row in b.rows[:24]:
            if not b.checkable(row):
                continue
            self.assertNotEqual(self.judge(row, spots)['class'], 'shown', row['claim_id'])
            off = [s for s in spots if not b.about[s] & b.relevant(row)]
            base = b.basis(row)
            allowed = max(check_shown.FREE_OFF_SUBJECT, len(base))
            if len(off) > allowed:
                got = self.judge(row, base + off[:allowed + 1])
                self.assertEqual((got['class'], got['kind']), ('right_not_shown', 'overquoted'), row['claim_id'])
                got = self.judge(row, base + off[:allowed])
                self.assertEqual(got['class'], 'shown', row['claim_id'])
                hit += 1
        self.assertGreater(hit, 0)


class SuccessionNamesTests(unittest.TestCase):
    """`facts.names` of succession gives every berth label its own key (listing only itself) and every party its names
    (big_succession.py 8c935b1e; before, a renumbered berth had one key, its first number, listing all its numbers, so
    the present number that a `part` statement asks for was no key and could not be read as the true value). On seeds 1
    to 8: every statement whose value is a name reads, through `Names` and `read_value`, as its true value; no
    refuted statement's true value reads as its stated value; and the count of statements that fail either is kept in
    `UNANSWERABLE`, which must be zero for every seed and, as it once was, only ever of `part` statements."""

    def lost(self, seed):
        b = Built.of('succession', seed)
        names, none = b.family_obj.names, b.module.NONE_VALUE
        lost = []
        for row in b.rows:
            true = b.true(row)
            if row['value_form'] != 'name' or true == NONE:
                continue
            read = check_shown.read_value(true, 'name', names)
            repeats = row['semantic_state'] == 'refuted' and check_shown.agrees(read, row['derivation']['stated'], 'name', none)
            if not check_shown.agrees(read, true, 'name', none) or repeats:
                lost.append(row)
        return lost

    def test_every_name_value_reads_as_itself_and_never_as_the_stated_value_of_a_refuted_statement(self):
        for seed in range(1, 9):
            lost = self.lost(seed)
            UNANSWERABLE[seed] = len(lost)
            self.assertEqual([(row['claim_id'], row['query']['kind']) for row in lost], [], seed)
        self.assertEqual(UNANSWERABLE, {seed: 0 for seed in range(1, 9)})

    def test_a_number_a_berth_once_had_is_not_its_present_number(self):
        """The label keys do not merge: the earlier number of a renumbered berth reads as itself and not as the present
        one (a statement that gives the earlier number is refuted, and typing it must not agree with the truth)."""
        found = 0
        for seed in range(1, 9):
            b = Built.of('succession', seed)
            renumbered = [e for e in b.events if e['kind'] == 'renumber']
            for event in renumbered:
                old, new = event['old'], event['label']
                if old == new:
                    continue
                read = check_shown.read_value(old, 'name', b.family_obj.names)
                self.assertFalse(check_shown.agrees(read, new, 'name', b.module.NONE_VALUE), (seed, old, new))
                self.assertTrue(check_shown.agrees(read, old, 'name', b.module.NONE_VALUE), (seed, old, new))
                found += 1
        self.assertGreater(found, 0)


class NamingPassageTests(unittest.TestCase):
    """A paragraph with its own passage role (`names`) that fixes the name by which another paragraph tells its event
    earns that naming event and is on the subject: the checker needs nothing new for it (`EARNS_NOTHING` stays
    `titles`). Hand case 'the paragraph above' as a dossier of its own."""

    def setUp(self):
        module = importlib.import_module('big_minutes')
        item = next(c for c in MINUTES_CASES if c['name'] == 'the paragraph above')
        words = ['alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike',
                 'november oscar papa quebec romeo sierra tango uniform victor whiskey xray yankee',
                 'zulu amber basalt cobalt dahlia ember fennel garnet heather indigo jasper kestrel',
                 'lantern meadow nectar orchid pewter quartz russet saffron thistle umber velvet walnut',
                 'yarrow zephyr acorn birch cedar dune elm fig gorse hazel iris juniper laurel maple',
                 'nutmeg onyx plum quince raven sable tulip umbra violet willow xenon yew zinnia aster',
                 'bramble clover daisy elder fennel ginger holly ivy jade kale lilac mint nettle olive']
        self.blocks = check_shown.split_paragraphs('\n\n'.join(words) + '\n')
        self.sources = check_shown.Sources({'book.md': '\n\n'.join(words) + '\n'})
        self.item = item
        roles = lambda e: 'names' if e['kind'] == 'names' else 'states'  # noqa: E731
        passages = [{'file': 'book.md', 'paragraph': n, 'event_ids': [e['id']], 'role': roles(e)}
                    for n, e in enumerate(item['events'])]
        self.family = check_shown.Family(module, {'events': item['events'], 'passages': passages, 'names': {}},
                                         'minutes')
        self.query = item['queries'][0]                       # the total paid to A after meeting 3: 5,000

    def judge(self, events):
        row = {'claim_id': 'C01', 'variant': 'correct', 'semantic_state': 'supported', 'claim_kind': 'minutes_total',
               'query': self.query['query'], 'value_form': 'integer', 'derivation': {'stated': 5000, 'worlds': 1}}
        sheet = {'status': 'valid', 'rows': [{'claim_id': 'C01', 'semantic_state': 'supported', 'value': 5000,
                                             'evidence': [{'quote': self.blocks[int(e[1:]) - 1]} for e in events]}]}
        return check_shown.judge_review(self.family, [row], sheet, self.sources, 'answered')[0]

    def test_the_naming_paragraph_is_demanded_with_the_paragraph_that_points_to_it(self):
        self.assertEqual(self.judge(['T0002', 'T0003', 'T0004'])['class'], 'shown')
        got = self.judge(['T0003', 'T0004'])                  # the payment told "for the undertaking named above" alone
        self.assertEqual((got['class'], got['kind'], got['basis']['missing']), ('right_not_shown', 'reading', ['T0002']))

    def test_the_naming_paragraph_earns_its_event_and_is_on_the_subject(self):
        got = self.judge(['T0002', 'T0003', 'T0004'])
        self.assertEqual((got['basis']['needed_quotes'], got['basis']['off_subject']), (3, 0))
        got = self.judge(['T0002'])                           # the lead alone earns the naming event and nothing else
        self.assertEqual((got['basis']['cited'], got['basis']['off_subject'], got['basis']['missing']),
                         (1, 0, ['T0003', 'T0004']))
        got = self.judge(['T0006', 'T0007'])                  # the other project's lead and payment: off the subject
        self.assertEqual(got['basis']['off_subject'], 2)
        self.assertEqual(got['class'], 'right_not_shown')

    def test_a_naming_entry_in_a_passage_earns_unlike_a_heading(self):
        facts = {'events': [{'id': 'E1', 'kind': 'names'}, {'id': 'E2', 'kind': 'pay'}], 'names': {},
                 'passages': [{'file': 'a.md', 'paragraph': 0, 'event_ids': ['E1'], 'role': 'names'},
                              {'file': 'a.md', 'paragraph': 1, 'event_ids': ['E2'], 'role': 'titles'}]}
        family = check_shown.Family(self.family.module, facts, 'minutes')
        self.assertEqual(dict(family.earns), {('a.md', 0): {'E1'}})
        self.assertEqual(dict(family.about), {('a.md', 0): {'E1'}, ('a.md', 1): {'E2'}})
        self.assertEqual(check_shown.EARNS_NOTHING, ('titles',))


class MinutesNumberingTests(unittest.TestCase):
    """The checker's numbering of paragraphs is the family's: a passage points at a paragraph of the right meeting,
    and (for the greater part of them) at a paragraph that names the project."""

    def date_in(self, heading, iso):
        year, month, day = int(iso[:4]), int(iso[5:7]), int(iso[8:])
        months = importlib.import_module('big_minutes').MONTHS
        return iso in heading or (months[month - 1] in heading and str(year) in heading and re.search(
            r'(?<!\d)%d(?:st|nd|rd|th)?(?!\d)' % day, heading) is not None)

    def test_a_passage_is_in_the_meeting_of_its_event(self):
        checked = 0
        for seed in SEEDS['minutes'] + (3,):
            b = Built.of('minutes', seed)
            heading = {}
            last = None
            for file, blocks in b.original.items():
                for number, block in enumerate(blocks):
                    if block.startswith('## '):
                        last = block
                    heading[(file, number)] = last
            for passage in b.passages:
                spot = (passage['file'], passage['paragraph'])
                if passage['role'] != 'states':        # a correction or a return is minuted at a later meeting than its target
                    continue
                for event in passage['event_ids']:
                    self.assertTrue(self.date_in(heading[spot], b.by_id[event]['date']), (seed, spot, event))
                    checked += 1
        self.assertGreater(checked, 300)

    def test_most_passages_name_their_project_or_point_back_to_the_paragraph_that_does(self):
        module = importlib.import_module('big_minutes')
        seed = 1
        b = Built.of('minutes', seed)
        raw = module.build(random.Random(f'evalsuite:{generate.GENERATOR_VERSION}:{seed}:1:minutes'))
        self.assertEqual([e['id'] for e in raw['events']], [e['id'] for e in b.events])
        projects = {p['name']: p for p in raw['projects']}
        good = total = 0
        for passage in b.passages:
            file, number = passage['file'], passage['paragraph']
            for event in passage['event_ids']:
                e = b.by_id[event]
                if passage['role'] != 'states' or e['kind'] in ('intro', 'restate'):
                    continue
                p = projects[e['subject']]
                forms = [p['name']] + list(p['short_forms']) + ([p['sponsor_form']] if p['sponsor_form'] else [])
                text, above = b.original[file][number], b.original[file][number - 1]
                total += 1
                good += (any(f in text for f in forms) or 'undertaking to receive the committee\u2019s backing' in text
                         or (any(a in text for a in module.ANAPHORS) and any(f in above for f in forms)))
        self.assertGreater(total, 100)
        self.assertGreater(good / total, 0.9)


def sweep(family, seed):
    """Two sweeps over one dossier (both reports): (i) a basis made only of paragraphs that state none of a statement's
    needed events is never `shown`; (ii) a basis of one stating paragraph for each needed event (and, for a count of
    which one of several provisions would do, enough more) always is. Returns counts and the statements that fail."""
    b = Built.of(family, seed)
    counts, failures = Counter(), []

    def judge(row, spots):
        sheet = {'status': 'valid', 'rows': [{'claim_id': row['claim_id'], 'semantic_state': row['semantic_state'],
                                             'value': render(b, row, b.true(row)),
                                             'evidence': [{'quote': b.original[f][n]} for f, n in spots]}]}
        return check_shown.judge_review(b.family_obj, [row], sheet, b.sources, 'answered')[0]
    stating = [spot for spot in sorted(b.listed) if spot[0] not in check_shown.NOT_SHOWABLE_FILES]
    for row in b.rows:
        needed, relevant = set(b.needed(row)), b.relevant(row)
        counts['statements'] += 1
        elsewhere = [s for s in stating if not b.listed[s] & needed and b.sources.locate(b.original[s[0]][s[1]])[0] == 'located']
        near = [s for s in elsewhere if b.about.get(s, set()) & relevant]
        for name, spots in (('elsewhere', elsewhere), ('relevant but not needed', near)):
            got = judge(row, spots)
            counts['(i) quoted paragraphs: ' + name] += len(spots)
            counts['(i) shown from ' + name] += got['class'] == 'shown'
            if got['class'] == 'shown':
                failures.append(('shown without a stating paragraph', seed, row['claim_id'], name))
        missing = [e for e in needed if not b.quotable(e)]
        if missing or not needed:
            counts['not checkable: a needed event cannot be quoted' if missing
                   else 'not checkable: no event is needed'] += 1
            if judge(row, b.basis(row))['class'] != check_shown.NOT_CHECKABLE:
                failures.append(('an unquotable statement is not called not checkable', seed, row['claim_id'], missing))
            continue
        got = judge(row, b.basis(row))
        counts['(ii) shown by one stating paragraph per needed event'] += got['class'] == 'shown'
        if got['class'] != 'shown':
            failures.append(('not shown by its stating paragraphs', seed, row['claim_id'], got['class'], got['detail']))
    return counts, failures


class SweepTests(unittest.TestCase):
    def test_no_statement_is_shown_without_a_paragraph_that_states_what_it_needs_and_each_can_be_shown(self):
        for family in FAMILIES:
            for seed in SWEEP_SEEDS[family]:
                counts, failures = sweep(family, seed)
                self.assertEqual(failures, [], (family, seed))
                self.assertGreater(counts['statements'], 0)
                self.assertEqual(counts['(ii) shown by one stating paragraph per needed event']
                                 + counts['not checkable: a needed event cannot be quoted']
                                 + counts['not checkable: no event is needed'], counts['statements'])
                if family == 'amendments':
                    self.assertEqual(counts['not checkable: a needed event cannot be quoted'], 0, (family, seed))
                    self.assertEqual(counts['not checkable: no event is needed'], 0, (family, seed))
                self.assertGreater(counts['(i) quoted paragraphs: elsewhere'], 50 * counts['statements'] // 2)


class RedundantWholeFileTests(unittest.TestCase):
    """Hand case 14: a count of parameters over the whole file where either of two provisions would do."""

    def setUp(self):
        module = importlib.import_module('big_amendments')
        item = next(c for c in HAND_CASES if c['name'] == 'count over the whole file: one of several would do')
        self.events, self.query = item['events'], item['queries'][0]
        words = ['alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima',
                 'mike november oscar papa quebec romeo sierra tango uniform victor whiskey xray',
                 'yankee zulu amber basalt cobalt dahlia ember fennel garnet heather indigo jasper',
                 'kestrel lantern meadow nectar orchid pewter quartz russet saffron thistle umber velvet',
                 'walnut yarrow zephyr acorn birch cedar dune elm fig gorse hazel iris']
        texts = {'a.md': '\n\n'.join(words) + '\n'}
        self.sources, self.blocks = check_shown.Sources(texts), check_shown.split_paragraphs(texts['a.md'])
        passages = [{'file': 'a.md', 'paragraph': n, 'event_ids': [e['id']], 'role': 'states'}
                    for n, e in enumerate(self.events)]
        self.family = check_shown.Family(module, {'events': self.events, 'passages': passages, 'names': {}},
                                         'amendments')

    def judge(self, events):
        row = {'claim_id': 'C01', 'variant': 'correct', 'semantic_state': 'supported', 'claim_kind': 'params',
               'query': self.query['query'], 'value_form': 'integer', 'derivation': {'stated': 2, 'worlds': 1}}
        sheet = {'status': 'valid', 'rows': [{'claim_id': 'C01', 'semantic_state': 'supported', 'value': 2,
                                             'evidence': [{'quote': self.blocks[int(e[1:]) - 1]} for e in events]}]}
        return check_shown.judge_review(self.family, [row], sheet, self.sources, 'answered')[0]

    def test_one_of_the_two_provisions_of_the_same_parameter_shows_it_and_neither_does_not(self):
        for events in (['E001', 'E002', 'E005', 'E003'], ['E001', 'E002', 'E005', 'E004']):
            self.assertEqual(self.judge(events)['class'], 'shown', events)
        got = self.judge(['E001', 'E002', 'E005'])
        self.assertEqual((got['class'], got['kind'], got['basis']['missing'], got['basis']['cited_answer_is_true']),
                         ('right_not_shown', 'reading', [], False))


class ValueTests(unittest.TestCase):
    NAMES = check_shown.Names({'Head of Legal': ['Head of Legal', "the Customer's Head of Legal",
                                                 'the Head of Legal of the Supplier'],
                               'Finance Director': ['Finance Director', "the Supplier's Finance Director"]})

    def read(self, raw, form):
        return check_shown.read_value(raw, form, self.NAMES)

    def agrees(self, raw, target, form):
        return check_shown.agrees(self.read(raw, form), target, form, NONE)

    def test_integers_in_the_forms_the_note_allows(self):
        for raw in (1480, '1480', '1,480', '1 480', '€1,480', 'EUR 1480', '1480.00', ' 1480 ', 1480.0, '$ 1,480'):
            self.assertTrue(self.agrees(raw, 1480, 'integer'), raw)
        for raw in ('1,48', '14,80', '1480.5', 'about 1480', '1480 apples', True, False, float('nan'), float('inf'),
                    [1480], {'a': 1}, '', '1,4800', '-', '1.480,00'):
            self.assertFalse(self.agrees(raw, 1480, 'integer'), raw)
        self.assertIsNone(self.read('1,48', 'integer'))
        self.assertIsNone(self.read(True, 'integer'))
        self.assertIsNone(self.read(float('nan'), 'integer'))

    def test_units_and_a_full_stop_that_a_careful_agent_writes(self):
        for raw in ('90 days', '90 calendar days', '90 days.', '90.', 'EUR 1,480.', '1,480 euros', '1480 EUR', '\u00a31,480',
                    '1,480 pounds', '\u20ac 1480'):
            self.assertTrue(self.agrees(raw, 90 if raw.startswith('90') else 1480, 'integer'), raw)
        for raw in ('7.5 per cent', '7.5 percent', '7.5%', '7.5%.', '7.5 per cent.'):
            self.assertTrue(self.agrees(raw, 7.5, 'decimal'), raw)
        self.assertTrue(self.agrees('2034-07-19.', '2034-07-19', 'date'))
        self.assertTrue(self.agrees('2034-07.', '2034-07', 'month'))
        self.assertTrue(self.agrees('same.', 'same', ['choice', ['same', 'different']]))

    def test_five_hundred_point_zero_zero_zero_is_not_read_and_so_is_not_five_hundred(self):
        for form in ('integer',):
            self.assertIsNone(self.read('500.000', form))
            self.assertIsNone(self.read('1.480', form))
            self.assertIsNone(self.read('500.0000', form))
            self.assertFalse(self.agrees('500.000', 500, form))
        self.assertTrue(self.agrees('500.00', 500, 'integer'))
        self.assertTrue(self.agrees('500.0', 500, 'integer'))
        self.assertEqual(self.read('500.000', 'decimal'), ('ok', check_shown.Decimal('500.000')))

    def test_an_enormous_integer_is_read_at_once_and_is_nobody_s_value(self):
        import time
        started = time.time()
        for form in ('integer', 'decimal'):
            read = self.read(10 ** 400000, form)
            self.assertIsNotNone(read)
            self.assertFalse(check_shown.agrees(read, 1480, form, NONE))
            self.assertFalse(check_shown.agrees(read, NONE, form, NONE))
        self.assertIsNone(self.read(10 ** 400000, 'date'))
        self.assertLess(time.time() - started, 2)
        self.assertEqual(check_shown.printable(10 ** 400000), '<an integer of 1328772 bits>')
        self.assertEqual(check_shown.printable(-10 ** 30), '<an integer of 100 bits>')
        self.assertEqual(check_shown.printable(12345), 12345)

    def test_printable_values_can_always_be_written(self):
        for raw in ('\ud800', 'a\udfffb' * 200, float('nan'), float('inf'), 10 ** 500, 'x' * 10 ** 6, '\x00\u202e'):
            text = json.dumps(check_shown.printable(raw), ensure_ascii=False, allow_nan=False)
            text.encode('utf-8')
            self.assertLess(len(text), 2000)

    def test_decimals(self):
        for raw in (7.5, '7.5', '7.50', '7.5%', 7.50):
            self.assertTrue(self.agrees(raw, 7.5, 'decimal'), raw)
        self.assertTrue(self.agrees(5, 5, 'decimal'))
        self.assertTrue(self.agrees('5.0', 5, 'decimal'))
        self.assertFalse(self.agrees('7,5', 7.5, 'decimal'))
        self.assertFalse(self.agrees(7.4, 7.5, 'decimal'))

    def test_dates_and_months(self):
        self.assertTrue(self.agrees('2034-07-19', '2034-07-19', 'date'))
        for raw in ('2034-7-19', '19 July 2034', '2034-02-30', '2034-13-01', 20340719, '2034-07'):
            self.assertIsNone(self.read(raw, 'date'), raw)
        self.assertTrue(self.agrees('2034-07', '2034-07', 'month'))
        self.assertIsNone(self.read('2034-13', 'month'))

    def test_names_with_an_article_a_possessive_or_a_full_stop(self):
        for raw in ('the Head of Legal', "Customer's Head of Legal", 'Head of Legal of the Supplier',
                    'The Head of Legal of the Supplier.', "the customer\u2019s head of legal."):
            self.assertTrue(self.agrees(raw, 'Head of Legal', 'name'), raw)
        self.assertTrue(self.agrees("Supplier's Finance Director", 'Finance Director', 'name'))
        self.assertFalse(self.agrees("the Customer's Finance Director", 'Finance Director', 'name'))   # not a name given
        self.assertFalse(self.agrees("the Customer's Head of Legal", 'Finance Director', 'name'))

    def test_names_are_compared_with_every_name_the_file_gives(self):
        for raw in ('Head of Legal', "the Customer's Head of Legal", 'the head of legal of the supplier',
                    "the Customer’s Head of Legal", ' Head   of Legal. '):
            self.assertTrue(self.agrees(raw, 'Head of Legal', 'name'), raw)
        self.assertFalse(self.agrees('Finance Director', 'Head of Legal', 'name'))
        self.assertIsNone(self.read('Chief Executive', 'name'))
        self.assertIsNone(self.read(7, 'name'))

    def test_choices(self):
        form = ['choice', ['same', 'different']]
        self.assertTrue(self.agrees('same', 'same', form))
        self.assertTrue(self.agrees(' Same. ', 'same', form))
        self.assertFalse(self.agrees('different', 'same', form))
        self.assertIsNone(self.read('equal', form))
        self.assertTrue(self.agrees('different', 'different', ('choice', ('same', 'different'))))

    def test_none_is_right_only_where_there_is_no_value_and_null_never(self):
        for form in ('integer', 'decimal', 'date', 'name', ['choice', ['same', 'different']]):
            self.assertTrue(self.agrees('none', NONE, form), form)
            self.assertTrue(self.agrees(' None ', NONE, form), form)
            self.assertFalse(self.agrees(None, NONE, form), form)
            self.assertFalse(self.agrees('null', NONE, form), form)
        self.assertFalse(self.agrees('none', 1480, 'integer'))
        self.assertFalse(self.agrees(1480, NONE, 'integer'))
        self.assertFalse(self.agrees(0, NONE, 'integer'))
        self.assertEqual(self.read(None, 'integer'), check_shown.NULL)
        self.assertEqual(self.read('none', 'integer'), check_shown.NONE)
        self.assertNotEqual(check_shown.NONE, check_shown.NULL)

    def test_huge_and_odd_values_do_not_raise(self):
        for raw in ('9' * 100000, '1' * 61, 10 ** 400, 1e308, -1e308, '\x00', '‮1480', 'x' * 1000000, [], {}):
            for form in ('integer', 'decimal', 'date', 'month', 'name', ['choice', ['a']]):
                check_shown.agrees(self.read(raw, form), 1480, form, NONE)


class QuoteTests(unittest.TestCase):
    SOURCES = check_shown.Sources({
        'a.md': 'First paragraph, with plenty of words in it to quote from it.\n\n'
                'Second paragraph "with quotes" — and a dash and also the shared sentence that two paragraphs use.\n\n'
                'Third paragraph has the shared sentence that two paragraphs use as well.\n',
        'procedure.md': 'The reading rules that every statement is judged under are these.\n\nAnother rule of reading.\n'})

    def test_whitespace_quotes_and_dashes_are_normalised(self):
        locate = self.SOURCES.locate
        self.assertEqual(locate('Second  paragraph\n"with quotes" - and a dash and also'), ('located', ('a.md', 1)))
        self.assertEqual(locate('Second paragraph “with quotes” — and a dash and also'),
                         ('located', ('a.md', 1)))
        self.assertEqual(locate("First paragraph, with plenty of words in it to quote from it."), ('located', ('a.md', 0)))

    def test_forty_characters_are_measured_after_normalising(self):
        self.assertEqual(self.SOURCES.locate('First paragraph, with plenty of words in it'), ('located', ('a.md', 0)))
        self.assertEqual(self.SOURCES.locate('First paragraph, with plenty of words'), ('too_short', None))
        self.assertEqual(self.SOURCES.locate('First   paragraph,\n\nwith plenty of words in it'), ('located', ('a.md', 0)))

    def test_the_minimum_is_forty_characters_exactly(self):
        paragraph = 'First paragraph, with plenty of words in it to quote from it.'
        self.assertEqual(check_shown.MIN_QUOTE, 40)
        for length, want in ((38, 'too_short'), (39, 'too_short'), (40, 'located'), (41, 'located')):
            self.assertEqual(len(paragraph[:length]), length)
            self.assertEqual(self.SOURCES.locate(paragraph[:length])[0], want, length)
        # white space counts once, after normalising: 39 characters of text spread over more of them are still 39
        self.assertEqual(self.SOURCES.locate('First  paragraph,  with plenty of words i')[0], 'too_short')
        self.assertEqual(self.SOURCES.locate('First  paragraph,  with plenty of words in')[0], 'located')

    def test_across_two_paragraphs_and_reworded_quotes_are_not_found(self):
        self.assertEqual(self.SOURCES.locate('to quote from it. Second paragraph "with quotes" - and a dash'),
                         ('not_found', None))
        self.assertEqual(self.SOURCES.locate('First paragraph, with many words in it to quote from it.'),
                         ('not_found', None))

    def test_words_of_two_paragraphs_are_ambiguous_and_procedure_counts_for_nothing(self):
        self.assertEqual(self.SOURCES.locate('the shared sentence that two paragraphs use'), ('ambiguous', None))
        self.assertEqual(self.SOURCES.locate('The reading rules that every statement is judged under'), ('procedure', None))

    def test_paragraphs_are_the_blank_line_blocks(self):
        self.assertEqual(check_shown.split_paragraphs('a\n\nb\n \nc\n\n\n\nd\n'), ['a', 'b', 'c', 'd'])
        self.assertEqual(check_shown.split_paragraphs('a\r\n\r\nb\r\n'), ['a', 'b'])


# ---------------------------------------------------------------------------------------------------------
# The checker's numbering of paragraphs is the family's
# ---------------------------------------------------------------------------------------------------------

class NumberingTests(unittest.TestCase):
    def test_the_passage_map_points_at_the_paragraphs_the_family_says_it_does(self):
        checked = 0
        for family in FAMILIES:
            for seed in SEEDS[family]:
                b = Built.of(family, seed)
                for name, blocks in b.original.items():
                    self.assertEqual(len(blocks), len(b.sources.files[name]))
                heading = {}                                           # the last '## ... dated ...' heading of a paragraph
                last = None
                for number, block in enumerate(b.original.get('instruments.md', [])):
                    if block.startswith('## ') and ' dated ' in block:
                        last = block
                    heading[number] = last
                for passage in b.passages:
                    file, number = passage['file'], passage['paragraph']
                    self.assertTrue(0 <= number < len(b.original[file]), passage)
                    first = b.by_id[passage['event_ids'][0]]
                    text = b.original[file][number]
                    if file == 'agreement.md' and passage['role'] == 'titles':
                        self.assertEqual(text, '### %d. %s' % (first['clause'], first['heading']))
                        checked += 1
                    elif file == 'instruments.md' and first.get('instrument') and first.get('paragraph'):
                        self.assertIn(first['instrument'], heading[number], passage)
                        self.assertTrue(text.startswith('%d. ' % first['paragraph']), (passage, text[:40]))
                        checked += 1
        self.assertGreater(checked, 100)

    def test_the_checker_takes_the_sources_of_the_runner_and_the_family_numbers_the_same_blocks(self):
        for family in FAMILIES:
            b = Built.of(family, 1)
            for name, text in b.texts.items():
                self.assertEqual(len(check_shown.split_paragraphs(text)), len(b.sources.files[name]))
                self.assertEqual(text.count('\n\n') + 1, len(b.sources.files[name]), name)


# ---------------------------------------------------------------------------------------------------------
# (c) Properties of the interpreter and the key
# ---------------------------------------------------------------------------------------------------------

class PropertyTests(unittest.TestCase):
    def test_the_interpreter_agrees_with_the_key_for_every_statement_and_reader(self):
        for family in FAMILIES:
            for seed in PROPERTY_SEEDS[family]:
                b = Built.of(family, seed)
                for row in b.rows:
                    stated = row['derivation']['stated']
                    self.assertEqual('supported' if b.true(row) == stated else 'refuted', row['semantic_state'])
                    for name in b.readers:
                        got = b.module.answer(row['query'], b.events, name)
                        want = row.get('lure_states', {}).get(name, row['semantic_state'])
                        self.assertEqual('supported' if got == stated else 'refuted', want, (seed, row['claim_id'], name))

    def test_a_statement_with_an_empty_needed_set_is_not_checkable_and_the_checker_computes_the_same_set(self):
        """No event whose removal alone changes the value (two events either of which would do): the checker calls the
        statement not checkable (no basis can be required). Amendments and minutes have none such; counts of the others
        are kept in `EMPTY_NEEDED`."""
        for family in FAMILIES:
            for seed in PROPERTY_SEEDS[family]:
                b = Built.of(family, seed)
                fam = b.family_obj
                EMPTY_NEEDED[(family, seed)] = 0
                for row in b.rows:
                    needed = b.needed(row)
                    if not needed:
                        EMPTY_NEEDED[(family, seed)] += 1
                        self.assertNotIn(family, ('amendments', 'minutes'), (seed, row['claim_id']))
                        self.assertFalse(b.checkable(row))
                        sheet = {'status': 'valid', 'rows': [{'claim_id': row['claim_id'], 'semantic_state':
                                 row['semantic_state'], 'value': render(b, row, b.true(row))}]}
                        got = check_shown.judge_review(fam, [row], sheet, b.sources, 'answered')[0]
                        self.assertEqual((got['class'], got['detail']), (check_shown.NOT_CHECKABLE, 'no event is needed'))
                    analysis = fam.analysis(row['query'])
                    self.assertEqual(analysis['needed'], needed)
                    self.assertTrue(set(needed) <= b.relevant(row), (seed, row['claim_id']))   # the closed set, too
                    self.assertEqual(analysis['outside'], [])
                    self.assertTrue(set(b.needed_by_removal(row)) <= set(needed))

    def test_closing_the_needed_set_never_removes_an_event_and_changes_some_statements_of_amendments(self):
        changed, examples, total = 0, [], 0
        for family in FAMILIES:
            for seed in PROPERTY_SEEDS[family]:
                b = Built.of(family, seed)
                for row in b.rows:
                    removal, closed = b.needed_by_removal(row), b.needed(row)
                    self.assertTrue(set(removal) <= set(closed), (family, seed, row['claim_id']))
                    if family == 'amendments':
                        total += 1
                        if set(closed) != set(removal):
                            changed += 1
                            examples.append((seed, row['claim_id'], sorted(set(closed) - set(removal))))
        # Since the clause a provision names by its number or heading is a `target` and not a `name`, the closure adds
        # nothing to an amendments statement on seeds 1 to 8 (it is exercised by `ClosureTests` and the minutes family);
        # what is kept here is that it never takes an event away and how many statements it changes.
        CLOSURE_CHANGED['amendments'] = (changed, total)
        for seed, claim, added in examples[:1]:
            self.assertTrue(added)

    def test_removing_a_needed_event_changes_the_answer_and_adding_events_to_a_sufficient_set_does_not(self):
        rng = random.Random(7)
        for family in FAMILIES:
            for seed in PROPERTY_SEEDS[family][:3]:
                b = Built.of(family, seed)
                for row in b.rows[:len(b.rows) // 2]:
                    if not b.checkable(row):
                        continue
                    truth, needed = b.true(row), set(b.needed(row))
                    for event in b.needed_by_removal(row):
                        rest = [e for e in b.events if e['id'] != event]
                        self.assertNotEqual(b.module.answer(row['query'], rest), truth)
                    # a sufficient set: the cited events of a basis (the needed ones, and for a count of which any one
                    # of several provisions would do, enough more to give the count)
                    sufficient = b.cited(b.basis(row))
                    self.assertTrue(needed <= sufficient and b.answer_on(row, sufficient) == truth)
                    others = [e for e in b.events if e['id'] not in sufficient]
                    for _ in range(3):
                        extra = {e['id'] for e in rng.sample(others, min(len(others), rng.randint(1, 60)))}
                        self.assertEqual(b.answer_on(row, sufficient | extra), truth, (seed, row['claim_id']))

    def test_no_needed_event_is_one_whose_removal_only_makes_the_value_not_established(self):
        for family in FAMILIES:
            for seed in PROPERTY_SEEDS[family]:
                b = Built.of(family, seed)
                for row in b.rows:
                    for event in b.needed_by_removal(row):
                        rest = [e for e in b.events if e['id'] != event]
                        if b.module.answer(row['query'], rest) is None:
                            # only what the statement is about may do that: the clause of its own parameter, the
                            # award of its own project (spec 3.3, subset rule)
                            self.assertTrue(b.anchor(event, row), (family, seed, row['claim_id'], event))

    def test_the_needed_events_alone_give_the_value_except_where_one_of_several_would_do(self):
        completed, total = 0, 0
        for family in FAMILIES:
            for seed in PROPERTY_SEEDS[family]:
                b = Built.of(family, seed)
                for row in b.rows:
                    if not b.checkable(row):
                        continue
                    total += 1
                    if b.answer_on(row, set(b.needed(row))) != b.true(row):
                        completed += 1
                        self.assertTrue(b.sufficient(row, b.basis(row)), (seed, row['claim_id']))
        self.assertLess(completed, total)         # not a statement in general: a few count kinds only

    def test_every_statement_is_showable_or_is_not_checkable_for_a_reason_the_checker_names(self):
        """Every needed event has a paragraph that states it and a quote of which is located, or the checker calls the
        statement not checkable and names the event. Counts per family and seed are kept in `UNQUOTABLE` (reported, not
        asserted to be zero; amendments must stay at zero)."""
        for family in FAMILIES:
            for seed in PROPERTY_SEEDS[family]:
                b = Built.of(family, seed)
                count = 0
                for row in b.rows:
                    lost = b.unquotable(row)
                    sheet = {'status': 'valid', 'rows': [{'claim_id': row['claim_id'], 'semantic_state':
                             row['semantic_state'], 'value': render(b, row, b.true(row))}]}
                    got = check_shown.judge_review(b.family_obj, [row], sheet, b.sources, 'answered')[0]
                    if not b.needed(row):          # no event decides it by itself: not checkable, for that reason
                        self.assertEqual((got['class'], got['kind'], got['detail']),
                                         (check_shown.NOT_CHECKABLE, 'basis not checkable', 'no event is needed'))
                    elif lost:
                        count += 1
                        self.assertEqual((got['class'], got['kind']), (check_shown.NOT_CHECKABLE, 'basis not checkable'))
                        self.assertTrue(all(event in got['detail'] for event in lost[:6]), (got['detail'], lost))
                    else:
                        self.assertNotEqual(got['class'], check_shown.NOT_CHECKABLE, (family, seed, row['claim_id']))
                UNQUOTABLE[(family, seed)] = count
                if family == 'amendments':
                    self.assertEqual(count, 0, (family, seed))

    def test_the_answer_from_the_cited_events_of_a_basis_is_the_true_value(self):
        for family in FAMILIES:
            for seed in PROPERTY_SEEDS[family]:
                b = Built.of(family, seed)
                for row in b.rows:
                    if not b.checkable(row):
                        continue
                    cited = b.cited(b.basis(row))
                    self.assertTrue(set(b.needed(row)) <= cited)
                    self.assertEqual(b.module.answer(row['query'], [e for e in b.events if e['id'] in cited]),
                                     b.true(row), (seed, row['claim_id']))


# ---------------------------------------------------------------------------------------------------------
# (b) Stand-in answerers, through the real runner
# ---------------------------------------------------------------------------------------------------------

def right_row(b, row, spots=None, style=0, verdict=None):
    spots = b.basis(row) if spots is None else spots
    return sheet_row(row, verdict=verdict, value=render(b, row, b.true(row), style), spots=spots, built=b)


def mixed(rows_of, b, call):
    return {'claims': [rows_of(index, row) for index, row in enumerate(call['rows'])]}


def ambiguous_quote(b):
    """A text of at least 40 characters that occurs in more than one paragraph of the sources."""
    seen = {}
    for file, blocks in b.original.items():
        if file in check_shown.NOT_SHOWABLE_FILES:
            continue
        for number, block in enumerate(blocks):
            words = block.split()
            for start in range(0, max(0, len(words) - 8)):
                text = ' '.join(words[start:start + 8])
                if len(check_shown.normalise(text)) >= 40:
                    seen.setdefault(check_shown.normalise(text), set()).add((file, number))
    for text, where in sorted(seen.items()):
        if len(where) > 1 and b.sources.locate(text)[0] == 'ambiguous':
            return text
    return None


def procedure_quotes(b, count=3):
    out = []
    for number in range(len(b.original['procedure.md'])):
        spot = ('procedure.md', number)
        words = b.original['procedure.md'][number].split()
        for start in range(max(0, len(words) - 9)):
            text = ' '.join(words[start:start + 9])
            if b.sources.locate(text)[0] == 'procedure':
                out.append(text)
                break
        if len(out) == count:
            break
    return out


def wrong_value(b, row):
    """A value in the right form that is neither the true value nor the value the statement states."""
    import datetime
    true, stated = b.true(row), row['derivation']['stated']
    kind = row['value_form'] if isinstance(row['value_form'], str) else row['value_form'][0]
    if kind in ('integer', 'decimal'):
        candidates = [(0 if true == NONE else true) + step for step in (7, 13, 29, 101)]
    elif kind == 'date':
        base = datetime.date.fromisoformat(stated if stated != NONE else '2031-01-01')
        candidates = [(base + datetime.timedelta(days=step)).isoformat() for step in (11, 23, 37)]
    elif kind == 'choice':
        candidates = list(row['value_form'][1])
    else:
        candidates = list(b.names)
    return next(c for c in candidates + ['none', 'null'] if c not in (true, stated))


class StandIn:
    """A stand-in: the sheet it writes for a review, and the class and kind each statement must come out as."""

    def __init__(self, name, sheet, expect, applies=None):
        self.name, self.sheet, self.expect = name, sheet, expect
        self.applies = applies


SHOWN = ('shown', 'none')


def build_standins(b):
    """All stand-ins for one dossier. `sheet(call)` returns the policy outcome; `expect(row, index)` the (class, kind)."""
    out = []

    def standin(name, rows_of, expect, **kwargs):
        out.append(StandIn(name, lambda call: {'sheet': mixed(rows_of, b, call)}, expect, **kwargs))

    standin('perfect', lambda i, row: right_row(b, row), lambda row, i: SHOWN)

    def subject_spots(row):
        base = b.basis(row)
        relevant = b.relevant(row)
        more = [spot for spot in sorted(b.listed) if spot[0] not in check_shown.NOT_SHOWABLE_FILES
                and b.about[spot] & relevant and b.quote(spot) and spot not in base]
        return base + more
    standin('everything about the subject', lambda i, row: right_row(b, row, subject_spots(row)),
            lambda row, i: SHOWN)
    standin('other paragraphs for the same events', lambda i, row: right_row(b, row, b.basis(row, pick=-1)),
            lambda row, i: SHOWN)
    standin('paragraphs of other events', lambda i, row: right_row(
        b, row, [s for s in sorted(b.listed) if s[0] not in check_shown.NOT_SHOWABLE_FILES
                 and not b.about[s] & set(b.needed(row)) and b.quote(s)][:3]),
        lambda row, i: ('right_not_shown', 'reading'))

    def dependent(row):
        needed = b.needed(row)
        for event in needed:
            if any(event in list(b.by_id[other].get('requires', ())) + list(b.by_id[other].get('needs', ()))
                   + ([b.by_id[other]['ref']] if 'ref' in b.by_id[other] else []) for other in needed if other != event):
                return event
        return None

    def omit_spots(row):
        event = dependent(row)
        return b.basis(row) if event is None else b.basis(row, avoid=(event,))

    def omit_expect(row, i):
        return SHOWN if b.sufficient(row, omit_spots(row)) else ('right_not_shown', 'reading')
    standin('leaves out a passage another depends on', lambda i, row: right_row(b, row, omit_spots(row)), omit_expect)

    everything = [spot for spot in sorted(
        (f, n) for f, blocks in b.original.items() for n in range(len(blocks))) if b.quote(spot)
        or (spot[0] in check_shown.NOT_SHOWABLE_FILES)]

    # A quote from every paragraph of every source does not fit in the runner's sheet (2,000,000 bytes) once there is a
    # row for every statement; the stand-in quotes as many paragraphs as fit in 1,200,000 bytes, spread over the file.
    shared = [(file, b.quote(spot)) for spot in everything for file in [spot[0]]
              if file not in check_shown.NOT_SHOWABLE_FILES]

    def every_quote(row, rows=len(b.rows) // 2):
        budget = 1_200_000 // rows // 200
        step = max(1, len(shared) // budget)
        own = [(file, b.quote((file, number))) for file, number in b.basis(row)]
        return sheet_row(row, value=render(b, row, b.true(row)), quotes=own + shared[::step])
    standin('one quote from every paragraph', lambda i, row: every_quote(row),
            lambda row, i: ('right_not_shown', 'overquoted'))

    def bad_quote(row, index):
        spot = (b.basis(row) or [None])[0]
        text = b.quote(spot)
        words = text.split()
        mode = index % 3
        if mode == 0:
            bad = ' '.join(words[:4] + ['moreover'] + words[4:])
        elif mode == 1:
            # the end of the paragraph and the start of its neighbour; where the paragraphs of a family are made of
            # sentences that recur (succession), the two may happen to be one paragraph elsewhere, so other cuts are tried
            blocks = b.original[spot[0]]
            neighbours = [n for n in (spot[1] + 1, spot[1] - 1) if 0 <= n < len(blocks)]
            options = [blocks[spot[1]][-size:] + '\n\n' + blocks[n][:size] for size in (30, 45, 60, 75) for n in neighbours]
            bad = next((o for o in options if b.sources.locate(o)[0] != 'located'), options[0])
        else:
            bad = check_shown.normalise(text)[:38]
        assert b.sources.locate(bad)[0] != 'located', bad
        return sheet_row(row, value=render(b, row, b.true(row)), quotes=[(spot[0], bad)])
    standin('reworded, cut across paragraphs, 38 characters', lambda i, row: bad_quote(row, i),
            lambda row, i: ('right_not_shown', 'format'))

    ambiguous = ambiguous_quote(b)

    def with_ambiguous(row):
        item = right_row(b, row)
        item['evidence'].append({'path': 'case/sources/instruments.md', 'quote': ambiguous})
        return item
    standin('a quote in two paragraphs, besides a sufficient basis', lambda i, row: with_ambiguous(row),
            lambda row, i: SHOWN)

    def omit_and_unlocated(row):
        item = right_row(b, row, omit_spots(row))
        item['evidence'].append({'path': 'case/sources/instruments.md', 'quote': ambiguous})
        return item
    standin('leaves one out and quotes in two paragraphs', lambda i, row: omit_and_unlocated(row),
            lambda row, i: SHOWN if b.sufficient(row, omit_spots(row)) else ('right_not_shown', 'format'))

    standin('no value, no evidence (today\'s sheet)', lambda i, row: sheet_row(row), lambda row, i: ('verdict_only', 'format'))
    standin('evidence with a path and an anchor and no quote, procedure.md cited, no value',
            lambda i, row: sheet_row(row, extra={'evidence': [
                {'path': 'case/sources/procedure.md', 'anchor': 'P-00'},
                {'path': 'case/sources/agreement.md', 'anchor': 'A-00'}]}),
            lambda row, i: ('verdict_only', 'format'))
    standin('a value and evidence with no quote', lambda i, row: sheet_row(
        row, value=render(b, row, b.true(row)), extra={'evidence': [{'path': 'case/sources/procedure.md',
                                                                       'anchor': 'P-00'}]}),
            lambda row, i: ('right_not_shown', 'format'))
    standin('a value that cannot be read in its form', lambda i, row: sheet_row(
        row, value='about the usual amount', spots=b.basis(row), built=b), lambda row, i: ('verdict_only', 'format'))
    standin('a value that is not the true value', lambda i, row: sheet_row(
        row, value=wrong_value(b, row), spots=b.basis(row), built=b), lambda row, i: ('verdict_only', 'reading'))
    standin('three quotes from procedure.md besides a sufficient basis', lambda i, row: dict(
        right_row(b, row), evidence=right_row(b, row)['evidence'] + [
            {'path': 'case/sources/procedure.md', 'quote': q} for q in procedure_quotes(b)]),
            lambda row, i: SHOWN)
    verdicts = ('undetermined', 'both', 'not_applicable')
    standin('a verdict that is not definite', lambda i, row: right_row(b, row, verdict=verdicts[i % 3]),
            lambda row, i: ('withheld', 'reading'))
    standin('a definite verdict that is not the key\'s', lambda i, row: right_row(
        b, row, verdict='refuted' if row['semantic_state'] == 'supported' else 'supported'),
            lambda row, i: ('wrong', 'reading'))

    def two_rows(row):
        return [right_row(b, row), right_row(b, row)]
    out.append(StandIn('two perfect rows for one statement', lambda call: {'sheet': {'claims': [
        item for row in call['rows'] for item in two_rows(row)]}}, lambda row, i: ('verdict_only', 'format')))
    out.append(StandIn('a perfect row and an undetermined one', lambda call: {'sheet': {'claims': [
        item for row in call['rows'] for item in (right_row(b, row), right_row(b, row, verdict='undetermined'))]}},
                       # the scorer's merge puts refuted before undetermined, and undetermined before supported
                       lambda row, i: ('verdict_only', 'format') if row['semantic_state'] == 'refuted'
                       else ('withheld', 'reading')))
    def odd_ids(call):
        claims = []
        for i, row in enumerate(call['rows']):
            item = right_row(b, row)
            if i % 3 == 0:
                item['claim_id'] = '  ' + row['claim_id'] + ' \t'
            claims.append(item)
            if i % 5 == 0:
                claims.append(sheet_row(row, verdict='not_applicable'))
            if i % 4 == 0:
                claims.append(dict(right_row(b, row), claim_id='C99'))
        return {'sheet': {'claims': claims}}
    out.append(StandIn('padded ids, an unlisted id and a second row that is not applicable', odd_ids,
                       lambda row, i: SHOWN if i % 5 else (
                           ('withheld', 'reading') if row['semantic_state'] == 'supported' else ('verdict_only', 'format'))))
    out.append(StandIn('a missing row now and then', lambda call: {'sheet': {'claims': [
        right_row(b, row) for i, row in enumerate(call['rows']) if i % 5]}},
                       lambda row, i: ('unanswered', 'format') if i % 5 == 0 else SHOWN))

    def repeats_stated(row):
        stated = row['derivation']['stated']
        item = right_row(b, row)
        if row['semantic_state'] == 'refuted':
            item['value'] = render(b, row, stated)
        return item
    standin('repeats the stated value of every statement', lambda i, row: repeats_stated(row),
            lambda row, i: ('verdict_only', 'format') if row['semantic_state'] == 'refuted' else SHOWN)

    def none_style(row):
        return right_row(b, row)
    standin('none where there is no value', lambda i, row: none_style(row), lambda row, i: SHOWN)
    standin('null', lambda i, row: sheet_row(row, value=None, spots=b.basis(row), built=b),
            lambda row, i: ('verdict_only', 'reading'))
    standin('none for every value', lambda i, row: sheet_row(row, value='none', spots=b.basis(row), built=b),
            lambda row, i: SHOWN if b.true(row) == NONE else (
                ('verdict_only', 'format') if row['derivation']['stated'] == NONE else ('verdict_only', 'reading')))
    for style in range(5):
        standin(f'party under its name number {style}', lambda i, row, style=style: right_row(b, row, style=style),
                lambda row, i: SHOWN)
    standin('numbers as numbers and with a currency sign', lambda i, row: right_row(b, row, style=i), lambda row, i: SHOWN)

    def typed(got):
        """What an agent can write for a flawed reader's answer: the reader that ignores renaming marks its holder with a
        suffix (`RENAMED_SUFFIX`) that is no part of any name; the agent gives the name without it."""
        suffix = getattr(b.module, 'RENAMED_SUFFIX', None)
        return got[:-len(suffix)] if suffix and isinstance(got, str) and got.endswith(suffix) else got

    for reader in b.readers:
        def follows(row, reader=reader):
            got = b.module.answer(row['query'], b.events, reader)
            verdict = 'supported' if got == row['derivation']['stated'] else 'refuted'
            return sheet_row(row, verdict=verdict, value=render(b, row, typed(got)), spots=b.basis(row), built=b)

        def follows_expect(row, i, reader=reader):
            got = b.module.answer(row['query'], b.events, reader)
            verdict = 'supported' if got == row['derivation']['stated'] else 'refuted'
            if verdict != row['semantic_state']:
                return ('wrong', 'reading')
            return SHOWN if typed(got) == b.true(row) else ('verdict_only', 'reading')
        standin(f'the flawed reader {reader}', lambda i, row, follows=follows: follows(row), follows_expect)
    return out


def agent_copy_standin(b):
    """Edits its own copy of a source and quotes the edit: the runner's copy does not hold it."""
    def policy(call):
        claims, edits = [], {}
        for row in call['rows']:
            spot = (b.basis(row) or [None])[0]
            text = b.quote(spot)
            words = text.split()
            fake = ' '.join(words[:4] + ['definitely'] + words[4:])
            edits.setdefault(spot[0], []).append((text, fake))
            claims.append(sheet_row(row, value=render(b, row, b.true(row)), quotes=[(spot[0], fake)]))
        for file, pairs in edits.items():
            path = Path(call['cwd']) / 'case/sources' / file
            data = path.read_text(encoding='utf-8')
            for text, fake in pairs:
                data = data.replace(text, fake, 1)
            path.write_text(data, encoding='utf-8')
        return {'sheet': {'claims': claims}}
    return policy


class ShownCampaign(Campaign):
    """The shared campaign fixture over one dossier of a large family, with the facts in the key."""

    def __init__(self, root, family, seed, size=1):
        real = generate.write_suite

        def suite(fixtures, keys, number, dossiers, listed):
            with generate_big.facts_in_keys(), sized(size):
                return real(fixtures, keys, number, 1, [family])
        with mock.patch.object(generate, 'write_suite', suite):
            super().__init__(root, dossiers=1, seed=seed, home=True)

    def repeat_of(self, call):
        index = json.loads((self.out / 'plain/records/index.json').read_text(encoding='utf-8'))
        return index[Path(call['cwd']).name]['repeat']

    def run_arm(self, policy, repeats):
        patch = mock.patch.object(run_blind, 'toolchain_python', lambda *args, **kwargs: None)
        with patch, contextlib.redirect_stdout(io.StringIO()):
            return self.run(policy, arms='plain', repeats=repeats, arms_file=ARMS_FILE)

    def check(self, output=None, arm='plain'):
        output = output or self.root / 'shown.json'
        with contextlib.redirect_stdout(io.StringIO()):
            status = check_shown.main(['--campaign', str(self.out), '--keys', str(self.keys), '--arm', arm,
                                       '--output', str(output)])
        return status, json.loads(Path(output).read_text(encoding='utf-8')) if Path(output).exists() else None


class StandInCampaignTests(unittest.TestCase):
    """Every stand-in of the table, on every dossier, through the unchanged runner and then the checker."""

    @classmethod
    def setUpClass(cls):
        cls.runs = {}
        for family in FAMILIES:
            for seed in SEEDS[family]:
                b = Built.of(family, seed)
                standins = build_standins(b)
                campaign = ShownCampaign(fresh_folder(), family, seed)
                copy_index = len(standins) + 1

                def policy(call, standins=standins, campaign=campaign, b=b, copy_index=copy_index):
                    repeat = campaign.repeat_of(call)
                    if repeat == copy_index:
                        return agent_copy_standin(b)(call)
                    return standins[repeat - 1].sheet(call)
                status = campaign.run_arm(policy, len(standins) + 1)
                shown_status, shown = campaign.check()
                cls.runs[(family, seed)] = (b, campaign, standins, status, shown_status, shown)

    def reviews(self, shown, repeat):
        return [row for row in shown['rows'] if row['repeat'] == repeat]

    def test_the_campaigns_ran_and_the_checker_wrote_its_file(self):
        for key, (b, campaign, standins, status, shown_status, shown) in self.runs.items():
            self.assertEqual(status, 0, key)
            self.assertEqual(shown_status, 0, key)
            self.assertEqual(shown['warnings'], [], key)
            self.assertEqual(len(shown['per_review']), 2 * (len(standins) + 1), key)

    def test_every_statement_of_every_stand_in_comes_out_as_the_table_says(self):
        for key, (b, campaign, standins, status, shown_status, shown) in self.runs.items():
            for repeat, standin in enumerate(standins, 1):
                rows = self.reviews(shown, repeat)
                self.assertEqual(len(rows), len(b.rows), (key, standin.name))
                by_claim = {(r['variant'], r['claim_id']): r for r in rows}
                for variant in ('correct', 'mixed'):
                    key_rows = [r for r in b.rows if r['variant'] == variant]
                    for index, key_row in enumerate(key_rows):
                        got = by_claim[(variant, key_row['claim_id'])]
                        want = standin.expect(key_row, index)
                        if not b.checkable(key_row):       # no answer could show it, and none is blamed (3.3 step 4)
                            want = (check_shown.NOT_CHECKABLE, 'basis not checkable')
                        self.assertEqual((got['class'], got['kind']), want,
                                         (key, standin.name, variant, key_row['claim_id'], got['detail']))

    def test_the_perfect_stand_ins_cite_events_that_give_the_true_value(self):
        for key, (b, campaign, standins, status, shown_status, shown) in self.runs.items():
            for repeat in (1, 2, 3):
                for row in self.reviews(shown, repeat):
                    if row['class'] == check_shown.NOT_CHECKABLE:
                        continue
                    self.assertTrue(row['basis']['cited_answer_is_true'], (key, repeat, row['claim_id']))
                    self.assertEqual(row['basis']['missing'], [])
                    self.assertEqual(row['basis']['needed_outside_relevant'], [])

    def test_a_quote_in_two_paragraphs_is_counted_as_ambiguous(self):
        for key, (b, campaign, standins, status, shown_status, shown) in self.runs.items():
            repeat = [s.name for s in standins].index('a quote in two paragraphs, besides a sufficient basis') + 1
            rows = [r for r in self.reviews(shown, repeat) if r['class'] != check_shown.NOT_CHECKABLE]
            self.assertTrue(rows)
            self.assertTrue(all(r['quotes']['ambiguous'] == 1 and r['quotes']['located'] >= 1 for r in rows), key)

    def test_procedure_quotes_count_for_nothing(self):
        for key, (b, campaign, standins, status, shown_status, shown) in self.runs.items():
            repeat = [s.name for s in standins].index('three quotes from procedure.md besides a sufficient basis') + 1
            rows = [r for r in self.reviews(shown, repeat) if r['class'] != check_shown.NOT_CHECKABLE]
            self.assertTrue(rows)
            self.assertTrue(all(r['quotes']['procedure'] == 3 for r in rows), key)

    def test_the_flawed_readers_are_recorded(self):
        hits = 0
        for key, (b, campaign, standins, status, shown_status, shown) in self.runs.items():
            for reader in b.readers:
                repeat = [s.name for s in standins].index(f'the flawed reader {reader}') + 1
                for row in self.reviews(shown, repeat):
                    if row['class'] in ('wrong', 'verdict_only'):
                        self.assertIn(reader, row['flawed_readers'], (key, reader, row['claim_id']))
                        hits += 1
                    else:
                        self.assertNotIn('flawed_readers', row)
        self.assertGreater(hits, 0)           # over every dossier, so that a change of family does not empty it

    def test_the_branches_of_the_stand_ins_were_reached_somewhere(self):
        """A stand-in that does something only for some statements proves nothing if it never does it."""
        seen = Counter()
        for key, (b, campaign, standins, status, shown_status, shown) in self.runs.items():
            by_name = {s.name: n for n, s in enumerate(standins, 1)}
            for name, outcome, label in (
                    ('leaves out a passage another depends on', ('right_not_shown', 'reading'), 'left out'),
                    ('leaves one out and quotes in two paragraphs', ('right_not_shown', 'format'), 'left out, unlocated'),
                    ('everything about the subject', SHOWN, 'subject')):
                rows = self.reviews(shown, by_name[name])
                seen[label] += sum(1 for r in rows if (r['class'], r['kind']) == outcome)
            for variant in ('correct', 'mixed'):
                for index, row in enumerate([r for r in b.rows if r['variant'] == variant]):
                    kind = row['value_form'] if isinstance(row['value_form'], str) else row['value_form'][0]
                    true = b.true(row)
                    if kind == 'integer' and true != NONE and index % 4 in (1, 2, 3):
                        seen['integer in a form other than a JSON number'] += 1
                    if kind == 'name' and true != NONE and len(b.names.get(true, [])) > 1:
                        seen['name with more than one name'] += 1
                    seen['value forms: ' + kind] += 1
                    seen['no value'] += true == NONE
        for label in ('left out', 'left out, unlocated', 'subject', 'integer in a form other than a JSON number',
                      'name with more than one name', 'no value'):
            self.assertGreater(seen[label], 0, (label, dict(seen)))
        self.assertGreaterEqual(len([k for k in seen if k.startswith('value forms: ')]), 2)

    def test_the_command_line_gives_what_main_gives(self):
        """`check_shown.py` run as a command, once, on the first campaign."""
        import subprocess
        key = sorted(self.runs)[0]
        b, campaign, standins, status, shown_status, shown = self.runs[key]
        output = campaign.root / 'shown-cli.json'
        done = subprocess.run([sys.executable, str(ROOT / 'evalsuite/check_shown.py'), '--campaign', str(campaign.out),
                               '--keys', str(campaign.keys), '--arm', 'plain', '--output', str(output)],
                              capture_output=True, text=True, env={'PYTHONDONTWRITEBYTECODE': '1', 'PATH': '/usr/bin:/bin'})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn('verdict_and_value_right', done.stdout)
        self.assertEqual(json.loads(output.read_text(encoding='utf-8')), shown)


    def test_the_agent_s_own_copy_of_the_sources_is_never_searched(self):
        for key, (b, campaign, standins, status, shown_status, shown) in self.runs.items():
            rows = [r for r in self.reviews(shown, len(standins) + 1) if r['class'] != check_shown.NOT_CHECKABLE]
            self.assertTrue(rows)
            self.assertEqual({(r['class'], r['kind']) for r in rows}, {('right_not_shown', 'format')}, key)
            self.assertTrue(all(r['quotes']['not_found'] == 1 and r['quotes']['located'] == 0 for r in rows), key)

    def test_the_verdict_match_equals_the_one_of_compare_for_every_statement(self):
        for key, (b, campaign, standins, status, shown_status, shown) in self.runs.items():
            keys = scorecard.load_keys(campaign.keys)
            reviews = compare.arm_reviews(campaign.out, 'plain', keys)
            seen = 0
            for label, review in reviews.items():
                for row in review['rows']:
                    mine = [r for r in shown['rows'] if (r['dossier'], r['variant'], r['repeat'], r['claim_id']) == (
                        row['dossier'], row['variant'], row['repeat'], row['claim_id'])]
                    self.assertEqual(len(mine), 1)
                    self.assertEqual(mine[0]['verdict'], row['observed'], (key, label, row['claim_id']))
                    self.assertEqual(mine[0]['verdict_match'], row['matches'], (key, label, row['claim_id']))
                    seen += 1
            self.assertEqual(seen, len(shown['rows']))

    def test_totals_and_the_headline(self):
        for key, (b, campaign, standins, status, shown_status, shown) in self.runs.items():
            overall = shown['overall']['all']
            self.assertEqual(overall['statements'] + overall['not_checkable'], len(shown['rows']))
            self.assertEqual(sum(overall['classes'].values()), overall['statements'])
            self.assertEqual([list(h)[:3] for h in shown['headline']],
                             [['verdict_and_value_right', 'shown', 'wrong']] * 3)
            parts = shown['overall']
            for name in ('supported', 'refuted'):
                self.assertEqual(parts[name]['statements'], sum(1 for r in shown['rows'] if r['key_verdict'] == name
                                                                and r['class'] != check_shown.NOT_CHECKABLE))
            self.assertEqual(parts['supported']['statements'] + parts['refuted']['statements'], overall['statements'])
            perfect = [r for r in shown['per_review'].values() if r['repeat'] == 1][0]
            self.assertEqual(perfect['all']['shown']['count'], perfect['all']['statements'])
            self.assertEqual(shown['families'], {b.facts['interpreter']: b.facts['interpreter_sha256']})
            self.assertEqual(shown['checker']['sha256'],
                             hashlib.sha256((ROOT / 'evalsuite/check_shown.py').read_bytes()).hexdigest())
            self.assertEqual(shown['keys_manifest_sha256'],
                             hashlib.sha256((campaign.keys / 'KEYS-MANIFEST.json').read_bytes()).hexdigest())
            self.assertEqual(set(shown['per_kind']), {r['claim_kind'] for r in b.rows})
            for block in shown['per_kind'].values():
                self.assertEqual(set(block), {'all', 'supported', 'refuted'})


def heading_of(b, event):
    """The paragraph that heads an event's clause (a passage entry with the role `titles`), and its text."""
    for passage in b.passages:
        if passage['role'] in check_shown.EARNS_NOTHING and event in passage['event_ids']:
            return (passage['file'], passage['paragraph']), b.original[passage['file']][passage['paragraph']]
    return None, None


class HeadingTests(unittest.TestCase):
    """A heading names a thing and does not state it: a quote of it earns nothing (spec 3.3, step 3)."""

    def only_the_clause_statements(self, b):
        return [row for row in b.rows if row['variant'] == 'mixed' and len(b.needed(row)) == 1
                and b.by_id[b.needed(row)[0]]['kind'] == 'parameter']

    def test_a_quote_with_no_fact_in_it_is_not_shown_the_row_of_the_review(self):
        # seed 7, mixed C08 (a `first` statement whose needed set is only the clause) with the heading as its evidence
        b = Built.of('amendments', 7)
        rows = [r for r in self.only_the_clause_statements(b) if heading_of(b, b.needed(r)[0])[1]]
        self.assertTrue(rows)
        row = next((r for r in rows if r['claim_id'] == 'C08'), rows[0])
        spot, heading = heading_of(b, b.needed(row)[0])
        self.assertGreaterEqual(len(check_shown.normalise(heading)), 40)
        if row['claim_id'] == 'C08':
            self.assertEqual(heading, '### 114. Withdrawal of a termination notice')
        family = check_shown.Family(b.module, b.facts, 'amendments')
        sheet = {'status': 'valid', 'rows': [{'claim_id': row['claim_id'], 'semantic_state': row['semantic_state'],
                                             'value': b.true(row), 'evidence': [{'quote': heading}]}]}
        got = check_shown.judge_review(family, [row], sheet, b.sources, 'answered')[0]
        self.assertEqual((got['class'], got['kind']), ('right_not_shown', 'reading'))
        self.assertEqual(got['quotes']['located'], 1)
        self.assertEqual(got['basis']['missing'], b.needed(row))
        self.assertEqual(got['basis']['cited'], 0)
        self.assertEqual(got['basis']['off_subject'], 0)             # the heading is about a relevant event
        # the clause's own paragraph, which states the value, shows it
        states = b.quotable(b.needed(row)[0])
        self.assertTrue(states)
        sheet['rows'][0]['evidence'] = [{'quote': b.quote(states[0])}]
        self.assertEqual(check_shown.judge_review(family, [row], sheet, b.sources, 'answered')[0]['class'], 'shown')

    def test_no_statement_of_seeds_1_to_8_is_shown_by_headings_alone_or_by_a_heading_for_the_clause(self):
        quoted = 0
        for seed in range(1, 9):
            b = Built.of('amendments', seed)
            family = check_shown.Family(b.module, b.facts, 'amendments')
            titles = sorted({(p['file'], p['paragraph']) for p in b.passages if p['role'] in check_shown.EARNS_NOTHING
                             and len(check_shown.normalise(b.original[p['file']][p['paragraph']])) >= 40
                             and b.sources.locate(b.original[p['file']][p['paragraph']])[0] == 'located'})
            self.assertTrue(titles)
            for row in b.rows:
                heads = [{'quote': b.original[f][n]} for f, n in titles]
                swapped = [{'quote': b.quote(spot)} for event in b.needed(row)
                           if b.by_id[event]['kind'] != 'parameter' for spot in b.quotable(event)[:1]]
                swapped += [{'quote': heading_of(b, event)[1]} for event in b.needed(row)
                            if b.by_id[event]['kind'] == 'parameter' and heading_of(b, event)[1]]
                for evidence in (heads, swapped):
                    sheet = {'status': 'valid', 'rows': [{'claim_id': row['claim_id'], 'semantic_state':
                             row['semantic_state'], 'value': b.true(row), 'evidence': evidence}]}
                    got = check_shown.judge_review(family, [row], sheet, b.sources, 'answered')[0]
                    self.assertNotEqual(got['class'], 'shown', (seed, row['claim_id']))
                    quoted += got['quotes']['located']
        self.assertGreater(quoted, 0)

    def test_a_heading_is_on_the_subject_for_the_allowance_and_earns_nothing(self):
        family, sources, texts = StubFamilyTests().family()
        family.about[('a.md', 0)], family.earns[('a.md', 0)] = {'E1'}, set()
        self.assertEqual(family.earns.get(('a.md', 5), set()), set())
        facts = {'events': [{'id': 'E1', 'kind': 'x'}], 'names': {}, 'readers': [],
                 'passages': [{'file': 'a.md', 'paragraph': 0, 'event_ids': ['E1'], 'role': 'titles'},
                              {'file': 'a.md', 'paragraph': 1, 'event_ids': ['E1'], 'role': 'states'}]}
        fam = check_shown.Family(family.module, facts, 'stub')
        self.assertEqual((dict(fam.about), dict(fam.earns)), ({('a.md', 0): {'E1'}, ('a.md', 1): {'E1'}},
                                                              {('a.md', 1): {'E1'}}))


class PairingMixin:
    """The keys, the campaign and its sources belong together, or the checker refuses. One subclass for each family."""
    family = None

    @classmethod
    def setUpClass(cls):
        cls.b = b = Built.of(cls.family, 1)
        cls.campaign = campaign = ShownCampaign(fresh_folder(), cls.family, 1)
        campaign.run_arm(lambda call: {'sheet': {'claims': [right_row(b, row) for row in call['rows']]}}, 1)
        cls.status, cls.shown = campaign.check()

    def copy(self):
        """A copy of the campaign folder to damage."""
        target = fresh_folder() / 'out'
        shutil.copytree(self.campaign.out, target, symlinks=True)
        return target

    def reseal(self, edit):
        """The campaign's keys, sealed again after `edit(key)`: another manifest."""
        root = fresh_folder()
        (root / 'draft').mkdir()
        for path in self.campaign.draft.glob('*.json'):
            key = json.loads(path.read_text(encoding='utf-8'))
            edit(key)
            (root / 'draft' / path.name).write_text(json.dumps(key), encoding='utf-8')
        (root / 'arena').mkdir()
        blindness.seal_keys(root / 'draft', root / 'sealed', [root / 'arena'])
        return root / 'sealed'

    def point_at(self, out, keys):
        """Make the copy's campaign.json name the manifest of `keys`, as if the campaign had been run with them."""
        path = out / 'campaign.json'
        record = json.loads(path.read_text(encoding='utf-8'))
        record['keys_manifest_sha256'] = hashlib.sha256((Path(keys) / 'KEYS-MANIFEST.json').read_bytes()).hexdigest()
        path.write_text(json.dumps(record), encoding='utf-8')

    def sources_of(self, out):
        return next(Path(out).glob('plain/runs/*/input/sources'))

    def test_the_undamaged_campaign_is_checked(self):
        self.assertEqual(self.status, 0)
        self.assertEqual(self.shown['warnings'], [])

    def test_keys_that_are_not_those_of_the_campaign_are_refused_with_both_hashes(self):
        other = self.reseal(lambda key: None)                 # the same key, another canary: another manifest
        with self.assertRaises(check_shown.Refusal) as context:
            check_shown.run_check(self.campaign.out, other, 'plain')
        message = str(context.exception)
        self.assertIn('other keys', message)
        self.assertIn(hashlib.sha256((Path(other) / 'KEYS-MANIFEST.json').read_bytes()).hexdigest(), message)
        self.assertIn(hashlib.sha256((self.campaign.keys / 'KEYS-MANIFEST.json').read_bytes()).hexdigest(), message)

    def test_a_folder_that_is_no_campaign_of_the_runner_is_refused(self):
        out = self.copy()
        (out / 'campaign.json').unlink()
        with self.assertRaises(check_shown.Refusal):
            check_shown.run_check(out, self.campaign.keys, 'plain')

    def first_source(self):
        return sorted(name for name in self.b.facts['sources'] if name not in check_shown.NOT_SHOWABLE_FILES)[0]

    def test_sources_of_another_dossier_are_refused_whatever_the_passage_map_says(self):
        out = self.copy()
        folder = self.sources_of(out)
        for name, text in Built.of(self.family, 2).texts.items():          # the files of the dossier of seed 2
            (folder / name).write_text(text, encoding='utf-8')
        with self.assertRaises(check_shown.Refusal) as context:
            check_shown.run_check(out, self.campaign.keys, 'plain')
        self.assertIn('is not the file the key was made with', str(context.exception))
        self.assertIn(self.b.facts['sources'][self.first_source()], str(context.exception))

    def test_one_changed_character_or_one_more_file_in_the_sources_is_refused(self):
        out = self.copy()
        path = self.sources_of(out) / self.first_source()
        path.write_text(path.read_text(encoding='utf-8') + ' ', encoding='utf-8')
        with self.assertRaises(check_shown.Refusal) as context:
            check_shown.run_check(out, self.campaign.keys, 'plain')
        self.assertIn(self.first_source(), str(context.exception))
        out = self.copy()
        (self.sources_of(out) / 'extra.md').write_text('An extra file.\n', encoding='utf-8')
        with self.assertRaises(check_shown.Refusal) as context:
            check_shown.run_check(out, self.campaign.keys, 'plain')
        self.assertIn('extra.md', str(context.exception))

    def test_a_passage_map_that_names_a_paragraph_the_sources_do_not_have_is_refused(self):
        def beyond(key):
            key['facts']['passages'].append({'file': self.first_source(), 'paragraph': 99999,
                                             'event_ids': [key['facts']['events'][0]['id']], 'role': 'states'})
        keys = self.reseal(beyond)
        out = self.copy()
        self.point_at(out, keys)
        with self.assertRaises(check_shown.Refusal) as context:
            check_shown.run_check(out, keys, 'plain')
        self.assertIn('passage map', str(context.exception))

    def test_a_key_without_the_hashes_of_its_sources_or_its_readers_is_refused(self):
        for part in ('sources', 'readers'):
            keys = self.reseal(lambda key, part=part: key['facts'].pop(part))
            out = self.copy()
            self.point_at(out, keys)
            with self.assertRaises(check_shown.Refusal) as context:
                check_shown.run_check(out, keys, 'plain')
            self.assertIn('no facts', str(context.exception))

    def test_a_reader_the_dossier_does_not_have_is_refused_and_the_readers_of_the_facts_are_the_ones_recorded(self):
        b = self.b
        self.assertGreaterEqual(len(b.readers), 2)
        keep, other = b.readers[0], b.readers[1]
        keys = self.reseal(lambda key: key['facts'].update(readers=[keep]))
        with self.assertRaises(check_shown.Refusal) as context:
            check_shown.read_keys(keys)
        self.assertIn('readers the dossier does not have', str(context.exception))
        family = check_shown.Family(b.module, dict(b.facts, readers=[keep]), self.family)
        self.assertEqual(family.readers, [keep])
        row = next(r for r in b.rows if r['semantic_state'] == 'refuted' and other in r.get('lure_states', {}))
        got = b.module.answer(row['query'], b.events, other)
        sheet = {'status': 'valid', 'rows': [{'claim_id': row['claim_id'], 'semantic_state': 'supported',
                                             'value': render(b, row, got)}]}
        result = check_shown.judge_review(family, [row], sheet, b.sources, 'answered')[0]
        self.assertNotIn(other, result.get('flawed_readers', {}))


class PairingAmendments(PairingMixin, unittest.TestCase):
    family = 'amendments'

    def test_the_unpatched_checker_gives_the_same_rows(self):
        with mock.patch.object(check_shown, 'analyse', _ANALYSE), \
                mock.patch.object(check_shown, 'read_dependencies', _READ_DEPENDENCIES):
            self.assertIs(check_shown.analyse, _ANALYSE)
            plain = check_shown.run_check(self.campaign.out, self.campaign.keys, 'plain')
        self.assertEqual(plain['rows'], self.shown['rows'])
        self.assertEqual(plain['overall'], self.shown['overall'])

    def test_sources_of_another_size_are_refused_whatever_the_passage_map_says(self):
        out = self.copy()
        folder = self.sources_of(out)
        for name, text in Built.of(self.family, 1, 2).texts.items():       # the size-2 dossier's files
            (folder / name).write_text(text, encoding='utf-8')
        with self.assertRaises(check_shown.Refusal) as context:
            check_shown.run_check(out, self.campaign.keys, 'plain')
        self.assertIn('is not the file the key was made with', str(context.exception))
        self.assertIn(self.b.facts['sources']['agreement.md'], str(context.exception))


class PairingMinutes(PairingMixin, unittest.TestCase):
    family = 'minutes'


class PairingSuccession(PairingMixin, unittest.TestCase):
    family = 'succession'


class HostileSheetTests(unittest.TestCase):
    """Sheets that put what cannot be written into every field, through the runner and `main` to a real file."""

    @classmethod
    def setUpClass(cls):
        cls.b = b = Built.of(FAMILIES[0], 1)
        cls.campaign = campaign = ShownCampaign(fresh_folder(), FAMILIES[0], 1)
        rng = random.Random(5)
        huge = 10 ** 400000
        # Python 3.11 and later refuse to turn an integer of more than 4300 digits into text or text of that many
        # digits into an integer (`sys.get_int_max_str_digits()`). The fixture writes the sheet with the limit lifted;
        # the runner reads it under the limit of the interpreter, so there `json.loads` raises ValueError for the
        # sheet that holds `huge` and `read_sheet` calls the whole sheet unreadable. Python 3.9 reads it.
        try:
            json.loads('1' + '0' * 400000)
            cls.reads_huge = True
        except ValueError:
            cls.reads_huge = False
        within_the_limit = 10 ** 4000          # 4001 digits: read by both

        # A lone surrogate in a field the runner carries into findings.json (claim_id, explanation, a path, an anchor
        # or a quote) stops the runner itself at `write_json`, before this checker is reached. Only `value`, which the
        # runner does not carry, can hold one here.
        def surrogates(call):
            return {'claims': [dict(right_row(b, row), value='\ud800') for row in call['rows']]}

        def big_integers(call):          # a few of them fit in the 2,000,000 bytes the runner reads
            return {'claims': [dict(right_row(b, row), value=huge) if i < 2 else right_row(b, row)
                               for i, row in enumerate(call['rows'])]}

        def surrogates_in_every_value_form(call):
            values = ['\ud800', '\udfff' * 300, 'a\ud800b', '\ud83d', ['\ud800'], {'\ud800': 1}, None, True]
            return {'claims': [dict(right_row(b, row), value=values[i % len(values)])
                               for i, row in enumerate(call['rows'])]}

        def long_integers(call):          # as `big_integers`, with a number that every interpreter reads
            return {'claims': [dict(right_row(b, row), value=within_the_limit) if i < 2 else right_row(b, row)
                               for i, row in enumerate(call['rows'])]}

        def junk_sheet(call):
            return {'claims': [junk(rng, b, rng.choice(call['rows']), carried_surrogates=False,
                                    long_integers=cls.reads_huge) for _ in range(rng.randint(10, 60))]}

        cls.policies = [surrogates, big_integers, surrogates_in_every_value_form, junk_sheet, junk_sheet, long_integers]

        def policy(call):
            return {'sheet': cls.policies[campaign.repeat_of(call) - 1](call)}
        cls.status = campaign.run_arm(policy, len(cls.policies))
        import time
        started = time.time()
        cls.output = campaign.root / 'hostile-shown.json'
        cls.shown_status, cls.shown = campaign.check(cls.output)
        cls.seconds = time.time() - started

    def test_a_file_is_written_for_the_whole_arm_and_it_is_strict_json(self):
        self.assertEqual(self.status, 0)
        self.assertEqual(self.shown_status, 0)
        text = self.output.read_text(encoding='ascii')                  # nothing outside ASCII, whatever the sheets held
        def refuse(constant):
            raise AssertionError(constant)
        shown = json.loads(text, parse_constant=refuse)
        self.assertEqual(len(shown['per_review']), 2 * len(self.policies))
        self.assertEqual([p.name for p in self.output.parent.glob('hostile-shown.json*')], ['hostile-shown.json'])
        self.assertTrue(all(r['class'] in check_shown.CLASSES + (check_shown.NOT_CHECKABLE,) for r in shown['rows']))

    def test_the_values_are_copied_safely_and_in_the_right_class(self):
        by_repeat = {}
        for row in self.shown['rows']:
            by_repeat.setdefault(row['repeat'], []).append(row)
        for row in by_repeat[1]:                                           # a lone surrogate is an unreadable value
            self.assertEqual((row['class'], row['kind']), ('verdict_only', 'format'))
            self.assertEqual(row['value']['given'], '\\ud800')
        self.assert_integers_are_read_and_wrong(by_repeat[6])      # 4001 digits: read, on every interpreter
        if self.reads_huge:                                                # 400001 digits: read where it can be
            self.assert_integers_are_read_and_wrong(by_repeat[2])
        else:                                                              # and where it cannot, see the next test
            self.assertEqual({(r['class'], r['kind']) for r in by_repeat[2]}, {('unanswered', 'format')})
            self.assertFalse(any(str((r.get('value') or {}).get('given')).startswith('<an integer of') for r in by_repeat[2]))
        self.assertLess(self.seconds, 60)

    def assert_integers_are_read_and_wrong(self, rows):
        """An enormous integer in a sheet is read, and wrong (the first two rows of each variant carry one)."""
        for variant in ('correct', 'mixed'):
            mine = [r for r in rows if r['variant'] == variant]
            for index, row in enumerate(mine):
                if index < 2:
                    form = next(r for r in self.b.rows if (r['variant'], r['claim_id']) == (variant, row['claim_id']))[
                        'value_form']
                    number = form in ('integer', 'decimal')      # read and wrong; in any other form it cannot be read
                    self.assertEqual((row['class'], row['kind']), ('verdict_only', 'reading' if number else 'format'),
                                     row['claim_id'])
                    self.assertTrue(str(row['value']['given']).startswith('<an integer of '))
                else:
                    self.assertEqual(row['class'], 'shown')

    def test_a_sheet_with_an_integer_of_400001_digits_is_read_or_refused_whole_by_the_interpreter(self):
        limit = sys.get_int_max_str_digits() if hasattr(sys, 'get_int_max_str_digits') else 0
        self.assertEqual(self.reads_huge, limit == 0 or limit >= 400001)       # what the set-up found is the limit's doing
        units = [u for u in self.campaign.units().values() if u['repeat'] == 2]
        self.assertEqual(len(units), 2)                                        # a review of each report
        reviews = [r for r in self.shown['per_review'].values() if r['repeat'] == 2]
        self.assertEqual(len(reviews), 2)
        if self.reads_huge:                                                    # Python 3.9 to 3.10, or no limit
            self.assertEqual({(u['status'], u['sheet']) for u in units}, {('scored', 'valid')})
            self.assertEqual({(r['sheet'], r['answered']) for r in reviews}, {('valid', True)})
        else:       # the runner's `json.loads` raises ValueError: no row of the sheet is kept, not just the huge one.
            # This records what the runner does today on such an interpreter, a known limit and not a requirement:
            # if the runner comes to keep the other rows, these lines change with it.
            self.assertEqual({(u['status'], u['sheet']) for u in units}, {('failed', 'unreadable')})
            self.assertEqual({r['answered'] for r in reviews}, {False})
            self.assertEqual(self.status, 0)                                   # and the campaign goes on to the next review
            self.assertEqual({r['repeat'] for r in self.shown['per_review'].values()}, set(range(1, 7)))

    def test_an_output_folder_that_does_not_exist_is_made(self):
        root = fresh_folder()
        target = root / 'new' / 'deeper' / 'shown.json'
        status, shown = self.campaign.check(target)       # `main`, as `--output new/deeper/shown.json`
        self.assertEqual(status, 0)
        self.assertEqual(sorted(p.name for p in target.parent.iterdir()), ['shown.json'])
        self.assertEqual(json.loads(target.read_text(encoding='ascii'))['rows'], self.shown['rows'])
        # and the same through the command line, with a path that has one folder to make
        import subprocess
        again = root / 'cli' / 'shown.json'
        done = subprocess.run([sys.executable, str(ROOT / 'evalsuite/check_shown.py'), '--campaign',
                               str(self.campaign.out), '--keys', str(self.campaign.keys), '--arm', 'plain',
                               '--output', str(again)], capture_output=True, text=True,
                              env={'PYTHONDONTWRITEBYTECODE': '1', 'PATH': '/usr/bin:/bin'})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertNotIn('Traceback', done.stderr)
        self.assertTrue(again.is_file())

    def test_a_failed_write_into_a_folder_it_would_have_made_leaves_no_folder(self):
        root = fresh_folder()
        with mock.patch.object(os, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                check_shown.write_output(root / 'one' / 'two' / 'shown.json', {'rows': []})
        self.assertEqual(list(root.iterdir()), [])
        with self.assertRaises(ValueError):               # not strict JSON: nothing at all is made
            check_shown.write_output(root / 'three' / 'shown.json', {'rate': float('inf')})
        self.assertEqual(list(root.iterdir()), [])
        (root / 'there').mkdir()
        with mock.patch.object(os, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                check_shown.write_output(root / 'there' / 'shown.json', {'rows': []})
        self.assertEqual(list((root / 'there').iterdir()), [])       # a folder that was there stays

    def test_a_failed_write_leaves_neither_a_partial_file_nor_a_temporary_one(self):
        folder = fresh_folder()
        target = folder / 'shown.json'
        with mock.patch.object(os, 'replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                check_shown.write_output(target, {'rows': [1, 2, 3]})
        self.assertEqual(list(folder.iterdir()), [])
        with self.assertRaises(ValueError):                               # not strict JSON: nothing is written at all
            check_shown.write_output(target, {'rate': float('nan')})
        self.assertEqual(list(folder.iterdir()), [])
        check_shown.write_output(target, {'text': '\ud800\u00e9'})
        self.assertEqual(json.loads(target.read_text(encoding='ascii')), {'text': '\ud800\u00e9'})
        self.assertEqual([p.name for p in folder.iterdir()], ['shown.json'])


class SizeTwoTests(unittest.TestCase):
    """One amending-family dossier at size 2 (BIG_AMENDMENTS_SIZE=2): the perfect stand-in shows every statement and a
    sheet that quotes nothing shows none. Nothing about the slot kinds of the family is pinned."""
    family, seed, size = 'amendments', 1, 2

    @classmethod
    def setUpClass(cls):
        import time
        cls.b = b = Built.of(cls.family, cls.seed, cls.size)
        cls.campaign = campaign = ShownCampaign(fresh_folder(), cls.family, cls.seed, cls.size)

        def policy(call):
            if campaign.repeat_of(call) == 1:
                return {'sheet': {'claims': [right_row(b, row) for row in call['rows']]}}
            return {'sheet': {'claims': [sheet_row(row, value=render(b, row, b.true(row)), quotes=[])
                                         for row in call['rows']]}}
        cls.status = campaign.run_arm(policy, 2)
        started = time.time()
        cls.shown_status, cls.shown = campaign.check()
        cls.seconds = time.time() - started

    def test_the_size_is_in_the_facts_and_the_dossier_is_larger(self):
        self.assertEqual(self.b.facts['size'], 2)
        self.assertGreater(len(self.b.events), len(Built.of(self.family, self.seed, 1).events))
        self.assertEqual(self.status, 0)
        self.assertEqual(self.shown_status, 0)
        self.assertEqual(self.shown['warnings'], [])

    def test_the_perfect_stand_in_shows_every_statement_and_quoting_nothing_shows_none(self):
        rows = self.shown['rows']
        self.assertEqual(len(rows), 2 * len(self.b.rows))      # two reviews, each of every statement of its variant
        perfect = [r for r in rows if r['repeat'] == 1]
        nothing = [r for r in rows if r['repeat'] == 2]
        self.assertEqual(len(perfect), len(self.b.rows))
        self.assertEqual({(r['class'], r['kind']) for r in perfect}, {SHOWN})
        self.assertTrue(all(r['basis']['cited_answer_is_true'] and r['basis']['sufficient'] for r in perfect))
        self.assertEqual({(r['class'], r['kind'], r['detail']) for r in nothing},
                         {('right_not_shown', 'format', 'no quote given')})

    def test_checking_one_dossier_takes_under_a_minute(self):
        self.assertLess(self.seconds, 60, self.seconds)

    def test_every_statement_has_needed_events_and_the_basis_of_the_largest_whole_file_statement_is_found(self):
        whole = [r for r in self.shown['rows'] if r['repeat'] == 1 and
                 self.b.rows[0] and self.row_of(r)['query']['kind'] in self.b.module.WHOLE_FILE]
        self.assertTrue(whole)
        self.assertTrue(all(r['basis']['needed'] for r in self.shown['rows'] if 'basis' in r))
        self.__class__.largest = max(r['quotes']['paragraphs'] for r in whole)
        self.assertGreater(self.largest, 0)

    def row_of(self, shown_row):
        return next(r for r in self.b.rows if r['claim_id'] == shown_row['claim_id'] and r['variant'] == shown_row['variant'])


class FailedReviewTests(unittest.TestCase):
    """Reviews that end badly, through the runner: no sheet, a link, a file too big, a timeout, a launch error."""

    @classmethod
    def setUpClass(cls):
        cls.family, cls.seed = FAMILIES[0], 1
        cls.b = Built.of(cls.family, cls.seed)
        cls.campaign = ShownCampaign(fresh_folder(), cls.family, cls.seed)
        b, campaign = cls.b, cls.campaign

        def perfect_sheet(call):
            return {'claims': [right_row(b, row) for row in call['rows']]}

        def link(call):
            (Path(call['cwd']) / 'real-sheet.json').write_text(json.dumps(perfect_sheet(call)), encoding='utf-8')
            os.symlink('real-sheet.json', Path(call['cwd']) / run_blind.SHEET)
            return {}

        def huge(call):
            body = json.dumps(perfect_sheet(call))
            (Path(call['cwd']) / run_blind.SHEET).write_text(
                body[:-2] + ',"pad": "' + 'x' * (run_blind.SHEET_LIMIT + 10) + '"}', encoding='utf-8')
            return {}

        def garbage(call):
            (Path(call['cwd']) / run_blind.SHEET).write_text('{"claims": [', encoding='utf-8')
            return {}

        def no_usable_row(call):
            return {'sheet': {'claims': [{'claim_id': 'C01'}, {'semantic_state': 'supported'},
                                         {'claim_id': 'C02', 'semantic_state': 'maybe'}, 7, 'x']}}

        cls.shapes = [
            ('no sheet', lambda call: {}, ('unanswered', 'format')),
            ('a link in place of the sheet', link, ('unanswered', 'format')),
            ('a file over the limit', huge, ('unanswered', 'format')),
            ('half a file', garbage, ('unanswered', 'format')),
            ('no usable row', no_usable_row, ('unanswered', 'format')),
            ('time ran out, no sheet', lambda call: {'ended': 'timeout', 'exit_code': None}, ('unanswered', 'time')),
            ('time ran out, a perfect sheet', lambda call: {'sheet': perfect_sheet(call), 'ended': 'timeout',
                                                            'exit_code': None}, SHOWN),
            ('time ran out, a sheet of flipped verdicts', lambda call: {'sheet': {'claims': [right_row(
                b, row, verdict='refuted' if row['semantic_state'] == 'supported' else 'supported')
                for row in call['rows']]}, 'ended': 'timeout', 'exit_code': None}, ('wrong', 'reading')),
            ('time ran out, a partial sheet', lambda call: {'sheet': {'claims': [
                right_row(b, row) for i, row in enumerate(call['rows']) if i % 5]}, 'ended': 'timeout',
                'exit_code': None}, {('unanswered', 'time'), SHOWN[0:2]}),
            ('a perfect sheet and a non-zero exit', lambda call: {'sheet': perfect_sheet(call), 'ended': 'exit_nonzero',
                                                                  'exit_code': 1}, SHOWN),
        ]

        def policy(call):
            return cls.shapes[campaign.repeat_of(call) - 1][1](call)
        cls.status = campaign.run_arm(policy, len(cls.shapes))
        cls.shown_status, cls.shown = campaign.check()

    def test_each_shape_gives_every_statement_its_class(self):
        self.assertEqual(self.status, 0)
        self.assertEqual(self.shown_status, 0)
        for repeat, (name, _, want) in enumerate(self.shapes, 1):
            rows = [r for r in self.shown['rows'] if r['repeat'] == repeat]
            self.assertEqual(len(rows), len(self.b.rows), name)
            self.assertEqual({(r['class'], r['kind']) for r in rows}, want if isinstance(want, set) else {want}, name)
            self.assertEqual({r['out_of_time'] for r in rows}, {name.startswith('time ran out')}, name)

    def test_the_verdict_match_of_every_shape_equals_the_one_of_compare(self):
        reviews = compare.arm_reviews(self.campaign.out, 'plain', scorecard.load_keys(self.campaign.keys))
        for label, review in reviews.items():
            for row in review['rows']:
                mine = [r for r in self.shown['rows'] if (r['dossier'], r['variant'], r['repeat'], r['claim_id']) == (
                    row['dossier'], row['variant'], row['repeat'], row['claim_id'])]
                self.assertEqual((mine[0]['verdict'], mine[0]['verdict_match']), (row['observed'], row['matches']), label)

    def test_a_review_that_ran_out_of_time_with_a_sheet_is_scored_from_it_and_marked(self):
        names = [n for n, _, _ in self.shapes]
        rows = [r for r in self.shown['rows'] if r['repeat'] == names.index('time ran out, a perfect sheet') + 1]
        self.assertTrue(rows and all(r['verdict_match'] and r['class'] == 'shown' and r['out_of_time'] for r in rows))
        flipped = [r for r in self.shown['rows']
                   if r['repeat'] == names.index('time ran out, a sheet of flipped verdicts') + 1]
        self.assertTrue(flipped and all(r['class'] == 'wrong' and r['out_of_time'] for r in flipped))
        for review in self.shown['per_review'].values():
            self.assertEqual(review['out_of_time'], review['ended'] == 'timeout')

    def test_rows_missing_from_a_partial_sheet_of_a_review_that_ran_out_of_time_are_time_not_format(self):
        names = [n for n, _, _ in self.shapes]
        rows = [r for r in self.shown['rows'] if r['repeat'] == names.index('time ran out, a partial sheet') + 1]
        for variant in ('correct', 'mixed'):
            mine = [r for r in rows if r['variant'] == variant]
            self.assertTrue(mine)
            for index, row in enumerate(mine):
                want = ('unanswered', 'time') if index % 5 == 0 else ('shown', 'none')
                self.assertEqual((row['class'], row['kind']), want, (variant, row['claim_id']))

    def test_a_launch_error_is_never_read_even_with_a_sheet_on_disk(self):
        campaign = ShownCampaign(fresh_folder(), self.family, self.seed)
        b = self.b

        def policy(call):
            (Path(call['cwd']) / run_blind.SHEET).write_text(json.dumps(
                {'claims': [right_row(b, row) for row in call['rows']]}), encoding='utf-8')
            return {'ended': 'launch_error', 'exit_code': None}
        campaign.run_arm(policy, 1)
        status, shown = campaign.check()
        self.assertEqual(status, 0)
        self.assertTrue(shown['rows'])
        self.assertEqual({(r['class'], r['kind']) for r in shown['rows']}, {('unanswered', 'format')})
        reviews = compare.arm_reviews(campaign.out, 'plain', scorecard.load_keys(campaign.keys))
        self.assertTrue(all(row['observed'] == 'missing' for review in reviews.values() for row in review['rows']))


# ---------------------------------------------------------------------------------------------------------
# (c) Fuzz: arbitrary sheets, and the refusals
# ---------------------------------------------------------------------------------------------------------

def junk(rng, b, row, carried_surrogates=True, long_integers=True):
    """A sheet row of an arbitrary shape. With `carried_surrogates` false, no lone surrogate is put where the runner
    copies it into findings.json (it cannot write one): in `value` only. With `long_integers` false, no integer of
    thousands of digits is put in `value`: the runner of Python 3.11 and later refuses a whole sheet that holds one."""
    pick = rng.choice
    big = 'x' * rng.choice((0, 1, 39, 40, 41, 10_000, 1_000_000))
    values = [None, True, False, 0, 1, -1, 3.5, 1e308, float('nan'), float('inf'), '', ' ', 'none', 'null', 'None',
              '1,480', '€1480', big, [], [1], {}, {'a': 1}, '2034-07-19', 10 ** 40, '9' * 5000, 'Head of Legal',
              '\ud800', 'a\udfffb', 10 ** 5000, -10 ** 5000, 2 ** 70, '\u202e500.000']
    if not long_integers:
        values = [v for v in values if not (type(v) is int and abs(v) > 10 ** 4000)]
    items = [None, 5, 'x', [], {}, {'path': 5}, {'quote': 5}, {'path': 'a', 'quote': big},
             {'path': '\ud800', 'quote': '\ud800' * 60} if carried_surrogates else {'path': 'p', 'quote': '\u00e9' * 60},
             {'quote': 'a\udfff' * 40} if carried_surrogates else {'quote': 'a\u00e9' * 40}, {'path': 'p', 'quote': None},
             {'quote': 'First paragraph with plenty of words in it to quote from it, again and again'},
             {'path': 'case/sources/agreement.md', 'quote': b.quote(rng.choice(sorted(b.listed)))
              if b.listed else 'x'}]
    out = {}
    if rng.random() < 0.95:
        out['claim_id'] = pick([row['claim_id'], ' ' + row['claim_id'] + ' ', row['claim_id'] + '\n', 7, None, '',
                                row['claim_id'] * 40, 'C99'])
    if rng.random() < 0.9:
        out['semantic_state'] = pick(['supported', 'refuted', 'both', 'undetermined', 'not_applicable', 'maybe', 3, None])
    if rng.random() < 0.8:
        out['value'] = pick(values)
    if rng.random() < 0.8:
        out['evidence'] = pick([None, 5, 'x', {}, [], [pick(items) for _ in range(rng.randint(0, 6))],
                                [pick(items) for _ in range(rng.randint(100, 400))]])
    return out


class FuzzTests(unittest.TestCase):
    def test_arbitrary_rows_never_raise_and_every_statement_gets_exactly_one_class(self):
        b = Built.of(FAMILIES[0], 1)
        family = check_shown.Family(b.module, b.facts, FAMILIES[0])
        family.readers = b.readers
        key_rows = [row for row in b.rows if row['variant'] == 'mixed']
        rng = random.Random(20261113)
        for trial in range(60):
            claims = []
            for _ in range(rng.randint(0, 80)):
                claims.append(junk(rng, b, rng.choice(key_rows)))
            rows = [c for c in claims if isinstance(c.get('claim_id'), str) and c['claim_id'].strip()
                    and isinstance(c.get('semantic_state'), str) and c['semantic_state'] in run_blind.STATES]
            sheet = {'status': 'valid' if rows else 'malformed', 'rows': rows, 'dropped': len(claims) - len(rows)}
            state = 'answered' if rows else 'format'
            results = check_shown.judge_review(family, key_rows, sheet, b.sources, state)
            self.assertEqual(len(results), len(key_rows))
            for result, row in zip(results, key_rows):
                self.assertEqual(result['claim_id'], row['claim_id'])
                self.assertIn(result['class'], check_shown.CLASSES)
                self.assertIn(result['kind'], ('none',) + check_shown.KINDS)
                json.dumps(result, allow_nan=False, ensure_ascii=True).encode('ascii')

    def test_the_sheet_the_runner_reads_is_the_one_the_checker_reads(self):
        folder = fresh_folder()
        (folder / run_blind.SHEET).write_text(json.dumps({'claims': [
            {'claim_id': ' C01 ', 'semantic_state': 'supported', 'value': 1},
            {'claim_id': 'C01', 'semantic_state': 'refuted'}, {'claim_id': 'C02', 'semantic_state': 'maybe'}]}))
        sheet = run_blind.read_sheet(folder)
        self.assertEqual(sorted(check_shown.sheet_rows(sheet)), ['C01'])
        self.assertEqual(len(check_shown.sheet_rows(sheet)['C01']), 2)
        self.assertEqual(scorecard.aggregate(check_shown.sheet_rows(sheet)['C01']), 'refuted')


class RefusalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = Built.of(FAMILIES[0], 1)

    def sealed(self, edit):
        """Seal a copy of the key after `edit(key)`, and return the sealed folder."""
        root = fresh_folder()
        key = copy.deepcopy(self.b.key)
        edit(key)
        (root / 'draft').mkdir()
        (root / 'draft' / f'{key["dossier_id"]}.json').write_text(json.dumps(key), encoding='utf-8')
        (root / 'arena').mkdir()
        blindness.seal_keys(root / 'draft', root / 'sealed', [root / 'arena'])
        return root / 'sealed'

    def refusal(self, edit):
        with self.assertRaises(check_shown.Refusal) as context:
            check_shown.read_keys(self.sealed(edit))
        return str(context.exception)

    def test_the_unchanged_key_is_read(self):
        self.assertEqual(sorted(check_shown.read_keys(self.sealed(lambda key: None))['dossiers']),
                         [self.b.key['dossier_id']])

    def test_another_module_hash_is_refused_and_both_hashes_are_named(self):
        message = self.refusal(lambda key: key['facts'].update(interpreter_sha256='0' * 64))
        self.assertIn('0' * 64, message)
        self.assertIn(self.b.facts['interpreter_sha256'], message)

    def test_a_statement_the_sources_leave_open_is_refused(self):
        def open_statement(key):
            key['expectations'][0]['semantic_state'] = 'undetermined'
        self.assertIn('leaves the statement open', self.refusal(open_statement))

        def two_worlds(key):
            key['expectations'][0]['derivation']['worlds'] = 2
        self.assertIn('unknown', self.refusal(two_worlds))

    def test_a_key_without_facts_or_with_an_interpreter_that_is_not_a_large_family_is_refused(self):
        self.assertIn('no facts', self.refusal(lambda key: key.pop('facts')))
        self.assertIn('not a large family', self.refusal(lambda key: key['facts'].update(interpreter='os')))
        self.assertIn('not a large family', self.refusal(lambda key: key['facts'].update(interpreter='../generate')))

    def test_a_row_the_interpreter_does_not_agree_with_is_refused(self):
        def flip(key):
            row = key['expectations'][0]
            row['semantic_state'] = 'refuted' if row['semantic_state'] == 'supported' else 'supported'
            row.pop('lure_states', None)
        self.assertIn('makes the statement', self.refusal(flip))

        def other_form(key):
            key['expectations'][0]['value_form'] = 'date'
        self.assertIn('value form', self.refusal(other_form))

        def lie_about_a_reader(key):
            row = next(r for r in key['expectations'] if 'reads_agreement_only' not in r.get('lure_states', {}))
            row.setdefault('lure_states', {})['reads_agreement_only'] = (
                'supported' if row['semantic_state'] == 'refuted' else 'refuted')
        self.assertIn('reader', self.refusal(lie_about_a_reader))

    def test_the_command_line_refuses_with_status_2_and_writes_nothing(self):
        root = fresh_folder()
        folder = self.sealed(lambda key: key['facts'].update(interpreter_sha256='f' * 64))
        output = root / 'shown.json'
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            status = check_shown.main(['--campaign', str(root), '--keys', str(folder), '--arm', 'plain',
                                       '--output', str(output)])
        self.assertEqual(status, 2)
        self.assertIn('Cannot check', err.getvalue())
        self.assertFalse(output.exists())

    def test_an_unsealed_folder_and_an_unknown_arm_are_refused(self):
        with self.assertRaises(check_shown.Refusal):
            check_shown.read_keys(fresh_folder())
        campaign = ShownCampaign(fresh_folder(), FAMILIES[0], 1)
        with self.assertRaises(check_shown.Refusal):
            check_shown.run_check(campaign.out, campaign.keys, 'skill-lean')


# ---------------------------------------------------------------------------------------------------------
# The checker with a family of its own (the kind of interpreter bug the checker must not hide)
# ---------------------------------------------------------------------------------------------------------

class ClosureTests(unittest.TestCase):
    """The needed set is closed under what fixes how an event's paragraph names its subject (spec 3.3 step 4, section 14).
    A stub family of the cancellation: H is the base fact; E and G are both named through F; E adds one and G takes one
    away, so that without F (E and G both left out) the value is the same as with it. F is then not needed by removal,
    though E and G are."""

    PARAGRAPHS = ['alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike',
                  'november oscar papa quebec romeo sierra tango uniform victor whiskey xray yankee',
                  'zulu amber basalt cobalt dahlia ember fennel garnet heather indigo jasper kestrel',
                  'lantern meadow nectar orchid pewter quartz russet saffron thistle umber velvet walnut']

    def module(self, with_depends=True):
        import types
        module = types.ModuleType('stub_cancel')
        module.NONE_VALUE = 'none'

        def answer(query, events, reader=None):
            ids = {e['id'] for e in events}
            if 'H' not in ids:
                return None
            return 5 + (('E' in ids) - ('G' in ids)) * ('F' in ids)
        module.answer = answer
        module.value_form = lambda query: 'integer'
        module.relevant = lambda query, events: [e['id'] for e in events]
        if with_depends:
            module.depends = lambda events: {e['id']: {'name': ['F'] if e['id'] in ('E', 'G') else [], 'amount': [],
                                                       'target': []} for e in events}
        return module

    def family(self, with_depends=True):
        events = [{'id': name, 'kind': 'x'} for name in 'HFEG']
        passages = [{'file': 'a.md', 'paragraph': n, 'event_ids': [e['id']], 'role': 'states'}
                    for n, e in enumerate(events)]
        family = check_shown.Family(self.module(with_depends), {'events': events, 'passages': passages, 'names': {}},
                                    'stub')
        return family, check_shown.Sources({'a.md': '\n\n'.join(self.PARAGRAPHS) + '\n'})

    def judge(self, names, with_depends=True):
        family, sources = self.family(with_depends)
        row = {'claim_id': 'C01', 'variant': 'correct', 'semantic_state': 'supported', 'claim_kind': 'stub',
               'query': {'kind': 'stub'}, 'value_form': 'integer', 'derivation': {'stated': 5, 'worlds': 1}}
        quotes = [{'quote': self.PARAGRAPHS['HFEG'.index(name)]} for name in names]
        sheet = {'status': 'valid', 'rows': [{'claim_id': 'C01', 'semantic_state': 'supported', 'value': 5,
                                             'evidence': quotes}]}
        return check_shown.judge_review(family, [row], sheet, sources, 'answered')[0]

    def test_the_removal_set_leaves_out_what_fixes_the_name_and_the_closed_set_has_it(self):
        family, _ = self.family()
        analysis = family.analysis({'kind': 'stub'})
        self.assertEqual(analysis['true'], 5)
        self.assertEqual(analysis['needed_by_removal'], ['H', 'E', 'G'])
        self.assertEqual(analysis['needed'], ['H', 'F', 'E', 'G'])
        self.assertEqual(analysis['outside'], [])

    def test_a_basis_that_quotes_the_event_without_what_fixes_its_name_is_not_shown(self):
        got = self.judge(['H', 'E', 'G'])
        self.assertEqual((got['class'], got['kind'], got['basis']['missing']), ('right_not_shown', 'reading', ['F']))
        self.assertTrue(got['basis']['cited_answer_is_true'])               # both conditions of the removal-only rule hold
        self.assertEqual(got['basis']['needed_by_removal'], ['H', 'E', 'G'])
        self.assertEqual(got['basis']['needed'], ['H', 'F', 'E', 'G'])
        got = self.judge(['H', 'F', 'E', 'G'])
        self.assertEqual((got['class'], got['kind']), ('shown', 'none'))

    def test_without_the_closure_that_basis_passes_which_is_what_the_ruling_stops(self):
        got = self.judge(['H', 'E', 'G'], with_depends=False)               # the family says nothing of dependencies
        self.assertEqual((got['class'], got['basis']['needed']), ('shown', ['H', 'E', 'G']))

    def test_a_fixer_added_by_the_closure_that_cannot_be_quoted_makes_the_statement_not_checkable(self):
        """The events needed by removal (H, E, G) all have a paragraph that can be quoted; F, added by the closure because
        E and G are named through it, has only a paragraph that occurs word for word elsewhere. The check is on the
        closed set, so the statement is not checkable and the detail names F."""
        family, sources = self.family()
        sources = check_shown.Sources({'a.md': '\n\n'.join(self.PARAGRAPHS + [self.PARAGRAPHS[1]]) + '\n'})
        row = {'claim_id': 'C01', 'variant': 'correct', 'semantic_state': 'supported', 'claim_kind': 'stub',
               'query': {'kind': 'stub'}, 'value_form': 'integer', 'derivation': {'stated': 5, 'worlds': 1}}
        analysis = family.analysis(row['query'])
        self.assertEqual(analysis['needed_by_removal'], ['H', 'E', 'G'])
        self.assertEqual(family.unquotable(analysis['needed_by_removal'], sources), [])
        self.assertEqual(family.unquotable(analysis['needed'], sources), ['F'])
        sheet = {'status': 'valid', 'rows': [{'claim_id': 'C01', 'semantic_state': 'supported', 'value': 5, 'evidence': [
            {'quote': self.PARAGRAPHS[n]} for n in (0, 2, 3)]}]}
        got = check_shown.judge_review(family, [row], sheet, sources, 'answered')[0]
        self.assertEqual((got['class'], got['kind']), (check_shown.NOT_CHECKABLE, 'basis not checkable'))
        self.assertIn('F', got['detail'])
        self.assertNotIn('E,', got['detail'])

    def test_only_names_are_followed_not_targets_or_amounts(self):
        events = [{'id': name, 'kind': 'x'} for name in 'ABCD']
        deps = {'A': {'name': ['B'], 'amount': ['C'], 'target': ['D']}, 'B': {'name': [], 'amount': [], 'target': []},
                'C': {'name': [], 'amount': [], 'target': []}, 'D': {'name': [], 'amount': [], 'target': []}}
        self.assertEqual(check_shown.close(['A'], deps, events), ['A', 'B'])
        deps['B']['name'] = ['C']
        self.assertEqual(check_shown.close(['A'], deps, events), ['A', 'B', 'C'])       # to the end
        self.assertEqual(check_shown.close([], deps, events), [])

    def test_a_family_without_depends_is_refused_by_the_checker(self):
        for module in (self.module(with_depends=False),):
            with self.assertRaises(check_shown.Refusal) as context:
                _READ_DEPENDENCIES(module, [{'id': 'H'}])
            self.assertIn('does not export depends', str(context.exception))
        module = self.module()
        for bad in (lambda events: [], lambda events: {'H': ['F']}, lambda events: {'H': {'name': 'F'}},
                    lambda events: {'H': {'name': [['F']]}}, lambda events: {'H': {'name': [{'F': 1}]}},
                    lambda events: {'H': {'target': [1]}}, lambda events: {'H': {'amount': [None]}},
                    lambda events: [{'F': 1}], lambda events: {1: {'name': []}}, lambda events: {('H',): {'name': []}}):
            module.depends = bad
            with self.assertRaises(check_shown.Refusal):
                _READ_DEPENDENCIES(module, [{'id': 'H'}])
        module.depends = lambda events: 1 / 0
        with self.assertRaises(check_shown.Refusal):
            _READ_DEPENDENCIES(module, [{'id': 'H'}])
        self.assertEqual(check_shown.INTERFACE, generate_big.INTERFACE)       # one list, with `depends` once in it
        self.assertEqual(check_shown.INTERFACE.count('depends'), 1)

    def test_the_keys_of_a_family_without_depends_are_refused(self):
        import types
        b = Built.of('amendments', 1)
        bare = types.SimpleNamespace(answer=b.module.answer, value_form=b.module.value_form, relevant=b.module.relevant,
                                     NONE_VALUE=b.module.NONE_VALUE, READERS=b.module.READERS)
        root = fresh_folder()
        (root / 'draft').mkdir()
        (root / 'draft' / f'{b.key["dossier_id"]}.json').write_text(json.dumps(b.key), encoding='utf-8')
        (root / 'arena').mkdir()
        blindness.seal_keys(root / 'draft', root / 'sealed', [root / 'arena'])
        with mock.patch.object(check_shown, 'read_dependencies', _READ_DEPENDENCIES):
            with self.assertRaises(check_shown.Refusal) as context:
                check_shown.read_keys(root / 'sealed', loader=lambda facts: (bare, facts['interpreter_sha256']))
        self.assertIn('does not export depends', str(context.exception))

    def test_every_family_that_is_required_to_export_depends_does(self):
        for name in DEPENDS_REQUIRED:
            self.assertTrue(callable(getattr(importlib.import_module(generate_big.BIG_FAMILIES[name]), 'depends', None)), name)


class QuotabilityTests(unittest.TestCase):
    """A needed event that no quote can establish makes the statement not checkable (spec 3.3 step 4): every paragraph
    that states it is shorter than 40 characters, or occurs word for word elsewhere, or it is stated nowhere. No answer
    could show such a statement, and none is blamed."""

    UNIQUE = 'The base fact is stated in this paragraph of the minute book, and nowhere else at all.'
    LEAD = 'A local resident had written to the committee about the noise from the Granary Arts Collective scheme.'
    OTHER = 'The second fact is stated in a paragraph of its own that no other paragraph of the book repeats.'

    def module(self):
        import types
        module = types.ModuleType('stub_quotable')
        module.NONE_VALUE = 'none'
        module.answer = lambda query, events, reader=None: None if not any(
            e['id'] == 'H' for e in events) else 5 + any(e['id'] == 'E' for e in events)
        module.value_form = lambda query: 'integer'
        module.relevant = lambda query, events: [e['id'] for e in events]
        return module

    def judge(self, paragraphs, passages, state='answered', out_of_time=False):
        events = [{'id': 'H', 'kind': 'x'}, {'id': 'E', 'kind': 'x'}]
        family = check_shown.Family(self.module(), {'events': events, 'passages': [
            {'file': 'a.md', 'paragraph': n, 'event_ids': [event], 'role': 'states'} for n, event in passages],
            'names': {}}, 'stub')
        sources = check_shown.Sources({'a.md': '\n\n'.join(paragraphs) + '\n'})
        row = {'claim_id': 'C01', 'variant': 'correct', 'semantic_state': 'supported', 'claim_kind': 'stub',
               'query': {'kind': 'stub'}, 'value_form': 'integer', 'derivation': {'stated': 6, 'worlds': 1}}
        sheet = {'status': 'valid', 'rows': [{'claim_id': 'C01', 'semantic_state': 'supported', 'value': 6, 'evidence': [
            {'quote': p} for p in paragraphs[:2]]}]}
        return check_shown.judge_review(family, [row], sheet, sources, state, out_of_time)[0]

    def test_a_needed_paragraph_that_occurs_word_for_word_elsewhere_makes_the_statement_not_checkable(self):
        got = self.judge([self.UNIQUE, self.LEAD, 'Other business.', self.LEAD], [(0, 'H'), (1, 'E')])
        self.assertEqual((got['class'], got['kind']), (check_shown.NOT_CHECKABLE, 'basis not checkable'))
        self.assertIn('E', got['detail'])
        self.assertNotIn('basis', got)
        self.assertEqual((got['verdict'], got['verdict_match']), ('supported', True))      # the verdict is still read

    def test_the_same_whatever_the_review_did(self):
        for state, out_of_time in (('format', False), ('time', True)):
            got = self.judge([self.UNIQUE, self.LEAD, self.LEAD], [(0, 'H'), (1, 'E')], state, out_of_time)
            self.assertEqual(got['class'], check_shown.NOT_CHECKABLE, state)

    def test_one_paragraph_that_can_be_quoted_is_enough(self):
        got = self.judge([self.UNIQUE, self.LEAD, self.LEAD, self.OTHER], [(0, 'H'), (1, 'E'), (3, 'E')])
        self.assertNotEqual(got['class'], check_shown.NOT_CHECKABLE)
        # the sheet quoted the ambiguous paragraph, which is no quote: the statement can be shown, this answer did not
        self.assertEqual((got['class'], got['basis']['missing']), ('right_not_shown', ['E']))

    def test_a_paragraph_under_forty_characters_and_a_statement_with_no_paragraph_are_not_quotable(self):
        got = self.judge([self.UNIQUE, 'Carried unanimously.', 'Other business.'], [(0, 'H'), (1, 'E')])
        self.assertEqual(got['class'], check_shown.NOT_CHECKABLE)
        got = self.judge([self.UNIQUE, self.OTHER], [(0, 'H')])
        self.assertEqual(got['class'], check_shown.NOT_CHECKABLE)
        self.assertEqual(check_shown.Sources({'a.md': 'x' * 39 + '\n\n' + 'y' * 40 + '\n'}).quotable(('a.md', 0)), False)
        self.assertEqual(check_shown.Sources({'a.md': 'x' * 39 + '\n\n' + 'y' * 40 + '\n'}).quotable(('a.md', 1)), True)

    def test_a_located_window_makes_the_paragraph_quotable_and_the_whole_paragraph_is_the_test(self):
        sources = check_shown.Sources({'a.md': 'First paragraph, with plenty of words in it to quote from it.\n\n'
                                              'Second paragraph, with plenty of words in it to quote from it.\n\n'
                                              'Third paragraph, with plenty of words in it to quote from it.\n'})
        for number in range(3):
            self.assertTrue(sources.quotable(('a.md', number)))
        duplicate = check_shown.Sources({'a.md': self.LEAD + '\n\n' + self.LEAD + '\n\n' + self.UNIQUE + '\n'})
        self.assertEqual([duplicate.quotable(('a.md', n)) for n in range(3)], [False, False, True])
        self.assertEqual(duplicate.locate(self.LEAD[10:70])[0], 'ambiguous')           # no window of it is located either


class AnswerTypesTests(unittest.TestCase):
    """What a family's `answer` returns for a subset is compared with the true value and used for nothing else: it may be
    a figure that is not whole, a string, a collection or not a number (spec 16). Nothing is formatted, sorted or
    written from it."""

    def test_odd_answers_for_a_subset_are_compared_and_not_written(self):
        import types
        odd = [2.5, float('nan'), float('inf'), (1, 2), {'a': 1}, 'x', object(), 10 ** 400, True, -0.0, None, 'none']
        for value in odd:
            module = types.ModuleType('stub_odd')
            module.NONE_VALUE = 'none'
            module.answer = lambda query, events, reader=None, value=value: 5 if len(events) == 2 else value
            module.value_form = lambda query: 'integer'
            module.relevant = lambda query, events: [e['id'] for e in events]
            events = [{'id': 'H', 'kind': 'x'}, {'id': 'E', 'kind': 'x'}]
            family = check_shown.Family(module, {'events': events, 'names': {}, 'passages': [
                {'file': 'a.md', 'paragraph': n, 'event_ids': [e['id']], 'role': 'states'} for n, e in enumerate(events)]},
                'stub')
            paragraphs = ['The base fact is stated in this paragraph of the minute book, and nowhere else at all.',
                          'The second fact is stated in a paragraph of its own that no other paragraph repeats.']
            sources = check_shown.Sources({'a.md': '\n\n'.join(paragraphs) + '\n'})
            row = {'claim_id': 'C01', 'variant': 'correct', 'semantic_state': 'supported', 'claim_kind': 'stub',
                   'query': {'kind': 'stub'}, 'value_form': 'integer', 'derivation': {'stated': 5, 'worlds': 1}}
            for quotes in (paragraphs, paragraphs[:1], []):
                sheet = {'status': 'valid', 'rows': [{'claim_id': 'C01', 'semantic_state': 'supported', 'value': 5,
                                                     'evidence': [{'quote': q} for q in quotes]}]}
                got = check_shown.judge_review(family, [row], sheet, sources, 'answered')[0]
                json.dumps(got, allow_nan=False, ensure_ascii=True)
                self.assertIn(got['class'], check_shown.CLASSES + (check_shown.NOT_CHECKABLE,), (value, quotes))
            full = check_shown.judge_review(family, [row], {'status': 'valid', 'rows': [{
                'claim_id': 'C01', 'semantic_state': 'supported', 'value': 5, 'evidence': [{'quote': q} for q in paragraphs]}]},
                sources, 'answered')[0]
            self.assertEqual(full['class'], 'shown', value)


class StubFamilyTests(unittest.TestCase):
    """A tiny family whose `relevant` leaves out an event that is needed: the needed set is computed over every event
    of the file, so the missing event still has to be cited."""

    def family(self):
        import types
        module = types.ModuleType('stub_family')
        module.NONE_VALUE = 'none'

        def answer(query, events, reader=None):
            ids = {e['id'] for e in events}
            if 'E1' not in ids:
                return None
            if 'E2' in ids and 'E3' in ids:
                return 20
            return None if 'E2' in ids else 10
        module.answer = answer
        module.value_form = lambda query: 'integer'
        module.relevant = lambda query, events: ['E1', 'E2']          # E3 is needed and not listed
        events = [{'id': 'E1', 'kind': 'x'}, {'id': 'E2', 'kind': 'x'}, {'id': 'E3', 'kind': 'x'}]
        passages = [{'file': 'a.md', 'paragraph': n, 'event_ids': [f'E{n + 1}'], 'role': 'states'} for n in range(3)]
        facts = {'family': 'stub', 'interpreter': 'stub_family', 'interpreter_sha256': '0' * 64, 'events': events,
                 'passages': passages, 'names': {}}
        family = check_shown.Family(module, facts, 'stub')
        texts = {'a.md': 'The first fact is stated in this paragraph of the agreement, plainly.\n\n'
                         'The second fact is stated in this paragraph of the agreement, plainly.\n\n'
                         'The third fact is stated in this paragraph of the agreement, plainly.\n'}
        return family, check_shown.Sources(texts), texts

    def row(self):
        return {'claim_id': 'C01', 'variant': 'correct', 'semantic_state': 'supported', 'claim_kind': 'stub',
                'query': {'kind': 'stub'}, 'value_form': 'integer', 'derivation': {'stated': 20, 'worlds': 1}}

    def judge(self, quotes):
        family, sources, texts = self.family()
        sheet = {'status': 'valid', 'rows': [{'claim_id': 'C01', 'semantic_state': 'supported', 'value': 20,
                                             'evidence': [{'path': 'a.md', 'quote': q} for q in quotes]}]}
        return check_shown.judge_review(family, [self.row()], sheet, sources, 'answered')[0]

    def test_a_needed_event_outside_the_relevant_list_is_still_needed(self):
        texts = self.family()[2]
        first, second, third = texts['a.md'].strip().split('\n\n')
        got = self.judge([first[:60], second[:60]])
        self.assertEqual((got['class'], got['kind']), ('right_not_shown', 'reading'))
        self.assertEqual(got['basis']['missing'], ['E3'])
        self.assertEqual(got['basis']['needed_outside_relevant'], ['E3'])
        got = self.judge([first[:60], second[:60], third[:60]])
        self.assertEqual((got['class'], got['kind']), ('shown', 'none'))

    def test_quoting_one_paragraph_many_times_does_not_raise_the_allowance_for_quotes_off_the_subject(self):
        family, sources, texts = self.family()
        family.module.answer = lambda query, events, reader=None: 10 if any(e['id'] == 'E1' for e in events) else None
        family.module.relevant = lambda query, events: ['E1']
        family.cache.clear()
        row = dict(self.row(), derivation={'stated': 10, 'worlds': 1})
        first = ('Alpha bravo charlie delta echo foxtrot golf hotel india juliet kilo lima mike november oscar papa '
                 'quebec romeo sierra tango uniform victor whiskey xray yankee zulu.')
        words = first.split()
        windows = [' '.join(words[start:start + 10]) for start in range(0, 5)]
        filler = ['This filler paragraph number %d says nothing about any fact at all.' % n for n in range(3)]
        rest = texts['a.md'].strip().split('\n\n')[1:]
        sources = check_shown.Sources({'a.md': '\n\n'.join([first] + rest + filler) + '\n'})
        sheet = {'status': 'valid', 'rows': [{'claim_id': 'C01', 'semantic_state': 'supported', 'value': 10,
                                             'evidence': [{'quote': q} for q in windows + filler]}]}
        got = check_shown.judge_review(family, [row], sheet, sources, 'answered')[0]
        self.assertEqual(got['quotes']['located'], 8)
        self.assertEqual((got['basis']['needed_quotes'], got['basis']['off_subject']), (1, 3))
        self.assertEqual((got['class'], got['kind']), ('right_not_shown', 'overquoted'))
        sheet['rows'][0]['evidence'] = [{'quote': q} for q in windows + filler[:2]]
        got = check_shown.judge_review(family, [row], sheet, sources, 'answered')[0]
        self.assertEqual((got['class'], got['kind']), ('shown', 'none'))

    def test_a_statement_with_an_empty_needed_set_cannot_be_checked_and_stands_outside_every_share(self):
        family, sources, texts = self.family()
        family.module.answer = lambda query, events, reader=None: 20
        sheet = {'status': 'valid', 'rows': [{'claim_id': 'C01', 'semantic_state': 'supported', 'value': 20}]}
        got = check_shown.judge_review(family, [self.row()], sheet, sources, 'answered')[0]
        self.assertEqual(got['class'], check_shown.NOT_CHECKABLE)
        rows = [dict(got, key_verdict='supported')]
        block = check_shown.block(rows)
        self.assertEqual((block['statements'], block['not_checkable'], block['shown']['of']), (0, 1, 0))

    def test_a_statement_with_an_empty_needed_set_is_not_checkable_whatever_the_answer_or_the_review(self):
        family, sources, texts = self.family()
        family.module.answer = lambda query, events, reader=None: 20
        right = {'claim_id': 'C01', 'semantic_state': 'supported', 'value': 20}
        for sheet, state, out_of_time in (
                ({'status': 'valid', 'rows': [dict(right, semantic_state='refuted')]}, 'answered', False),   # wrong
                ({'status': 'valid', 'rows': [dict(right, semantic_state='undetermined')]}, 'answered', False),
                ({'status': 'valid', 'rows': [dict(right, value=99)]}, 'answered', False),                   # wrong value
                ({'status': 'valid', 'rows': []}, 'answered', True),                                         # no row
                ({'status': 'missing', 'rows': []}, 'format', False)):                                       # failed
            got = check_shown.judge_review(family, [self.row()], sheet, sources, state, out_of_time)[0]
            self.assertEqual((got['class'], got['kind']), (check_shown.NOT_CHECKABLE, 'basis not checkable'), sheet)
            self.assertNotIn('basis', got)
        # the verdict of such a statement is still read, so that the match with the items file stays complete
        got = check_shown.judge_review(family, [self.row()], {'status': 'valid', 'rows': [
            dict(right, semantic_state='refuted')]}, sources, 'answered')[0]
        self.assertEqual((got['verdict'], got['verdict_match']), ('refuted', False))
        shares = check_shown.block([dict(got, key_verdict='supported')])
        self.assertEqual((shares['statements'], shares['wrong']['of'], shares['verdict_match']['of']), (0, 0, 0))


if __name__ == '__main__':
    unittest.main()
