#!/usr/bin/env python3
"""Check the answers of a finished campaign against the facts the generator invented. Calls no model.

    python3 evalsuite/check_shown.py --campaign OUT --keys SEALED --arm plain --output shown.json

The agent hands in, beside the verdict of a statement, the value it found and the passages the value rests on
(`evalsuite/prompts/task-shown.md`). This script judges each statement from the answer key's facts (the events the
generator invented, the passage map, the names, and the family's own interpreter) and never from the free text of
an explanation. It runs after a campaign, on the host that holds the keys. Read the output as answer material: it
holds true values and the events each statement rests on.

Every statement of every review gets exactly one class, by the first row of this table that applies:

     1  the review handed in no valid sheet (failed, or ran out of time without one), or the sheet
        has no usable row for the statement                                                       unanswered
     2  the verdict is supported or refuted and is not the key's                                  wrong
     3  the verdict is undetermined, both or not_applicable                                       withheld
     4  verdict right; value absent, unreadable in its form, or repeating the stated value of
        a refuted statement; or several rows for the statement                                   verdict_only (format)
     5  verdict right; value read and not the true value                                         verdict_only (reading)
     6  verdict and value right; no quote given, or none located                                  right_not_shown (format)
     7  ... not sufficient and a quote not found, ambiguous or too short                          right_not_shown (format)
     8  ... not sufficient and every quote located                                                right_not_shown (reading)
     9  ... sufficient but overquoted                                                             right_not_shown (overquoted)
    10  ... sufficient and not overquoted                                                         shown

A statement with an empty needed set, or with a needed event that no quote can establish (every paragraph that states it
is under 40 characters once normalised, or occurs word for word elsewhere, so that no quote of it is located), is
`not_checkable` whatever the answer, outside every share: no answer could show it and none is to be blamed.

A basis is sufficient when the cited events contain every needed event AND `answer(query, cited events)` is the true
value (the first alone would pass a basis that leaves out one of two provisions either of which would do). A review
that ran out of time but handed in a valid sheet is scored from that sheet; every row carries `out_of_time`.

A quote is searched, whitespace and typographic quotes and dashes normalised, in the runner's own copy of the sources
(`<run>/input/sources/`), never in the agent's copy. It is located when it has at least 40 characters (after
normalising) and lies inside exactly one paragraph of exactly one file; the path the agent gave plays no part. A
quote located only in procedure.md counts for nothing. A located paragraph earns every event the passage map lists
for it except through an entry whose role is `titles`: a heading names a thing and does not state it, so such an
entry earns nothing (the paragraph still counts as on the subject, being about a relevant event). Paragraphs are the
blank-line separated blocks of a file, numbered from 0, as the passage map numbers them.

"Needed" is computed here, by removing each event of the full list in turn and asking the family's interpreter
whether the answer changes, and then closed under the family's `depends`: with each such event goes what fixes how its
paragraph names its subject (the `name` dependencies), followed to the end, because removing a passage that fixes a
name can remove the event it fixes and something else with it, and the two effects can cancel. Targets and amounts are
not followed: a return and the payment it reverses cancel by design, and a count does not turn on amounts. The output
keeps the removal-only set as `basis.needed_by_removal`. "Relevant" is the family's own list. The number of quotes is the number of distinct
located paragraphs quoted: quoting one paragraph twice does not raise the allowance for quotes off the subject.
"""
import argparse
import bisect
from collections import Counter, defaultdict
import hashlib
import importlib
import json
import math
import os
from decimal import Decimal, InvalidOperation
from pathlib import Path
import re
import sys

sys.dont_write_bytecode = True
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import blindness  # noqa: E402
import generate_big  # noqa: E402
import run_blind  # noqa: E402
import scorecard  # noqa: E402

VERSION = 1
MIN_QUOTE = 40                  # characters, after normalising
FREE_OFF_SUBJECT = 2            # quotes off the subject that are always allowed
MAX_QUOTES = 2000               # quotes read from one row; the rest are counted as not found
MAX_TEXT = 100_000              # characters of a value or a quote that are looked at
NOT_SHOWABLE_FILES = ('procedure.md',)
EARNS_NOTHING = ('titles',)     # roles of a passage entry that earn no event: a heading names a thing, it does not state it
HUGE_BITS = 200                 # a JSON integer longer than this is read as "huge", not converted
CLASSES = ('unanswered', 'wrong', 'withheld', 'verdict_only', 'right_not_shown', 'shown')
NOT_CHECKABLE = 'not_checkable'
KINDS = ('format', 'reading', 'overquoted', 'time')
FORMS = ('integer', 'decimal', 'date', 'month', 'name', 'choice')
DEFINITE = ('supported', 'refuted')
INTERFACE = generate_big.INTERFACE     # what the launcher asks of a family: the checker asks the same (answer, value_form, relevant, depends, NONE_VALUE)


class Refusal(Exception):
    """The checker will not run on these inputs."""


# ---------- Text ----------

_TRANSLATE = {ord(c): "'" for c in '‘’‚‛'}
_TRANSLATE.update({ord(c): '"' for c in '“”„‟'})
_TRANSLATE.update({ord(c): '-' for c in '‐‑‒–—―−'})
_BLANK = re.compile(r'\n(?:[ \t]*\n)+')


def normalise(text):
    """Runs of white space to one space, typographic quotes and dashes to plain ones."""
    return ' '.join(text.translate(_TRANSLATE).split())


def split_paragraphs(text):
    """The blank-line separated blocks of a file, in order. The family numbers its blocks the same way."""
    return _BLANK.split(text.replace('\r\n', '\n').rstrip('\n'))


class Sources:
    """The paragraphs of every source file, searchable as one string so that no quote can span two paragraphs."""

    def __init__(self, texts):
        self.files = {name: [normalise(block) for block in split_paragraphs(text)] for name, text in sorted(texts.items())}
        self.order, self.starts, parts, position = [], [], [], 0
        for name, blocks in self.files.items():
            for number, block in enumerate(blocks):
                self.order.append((name, number))
                self.starts.append(position)
                parts.append(block)
                position += len(block) + 1
        self.hay = '\n'.join(parts)
        self._quotable = {}

    def locate(self, quote):
        """('located', (file, paragraph)), ('procedure', None), ('ambiguous', None), ('not_found', None) or
        ('too_short', None). The quote is normalised here."""
        quote = normalise(quote)
        if len(quote) < MIN_QUOTE:
            return 'too_short', None
        found, at = set(), self.hay.find(quote)
        while at != -1 and len(found) < 2:
            found.add(self.order[bisect.bisect_right(self.starts, at) - 1])
            at = self.hay.find(quote, at + 1)
        if not found:
            return 'not_found', None
        if all(name in NOT_SHOWABLE_FILES for name, _ in found):
            return 'procedure', None
        if len(found) > 1:
            return 'ambiguous', None
        return 'located', next(iter(found))


def _quotable(self, spot):
    """Whether some quote of the paragraph is located: it has at least 40 characters (normalised) and no other paragraph
    holds it. Any window of the paragraph that is located makes the whole paragraph located, and the whole paragraph
    is the longest window, so it is enough to ask of the whole paragraph. Cached."""
    if spot not in self._quotable:
        blocks = self.files.get(spot[0], ())
        text = blocks[spot[1]] if 0 <= spot[1] < len(blocks) else ''
        self._quotable[spot] = len(text) >= MIN_QUOTE and self.locate(text[:MAX_TEXT]) == ('located', spot)
    return self._quotable[spot]


Sources.quotable = _quotable


def read_sources(folder):
    """The bytes of every regular `*.md` file under a sources folder (links are not followed)."""
    folder, texts = Path(folder), {}
    if folder.is_symlink() or not folder.is_dir():
        raise Refusal(f'No sources folder: {folder}')
    for path in sorted(folder.rglob('*.md')):
        if path.is_symlink() or not path.is_file():
            continue
        texts[path.relative_to(folder).as_posix()] = path.read_bytes()
    if not texts:
        raise Refusal(f'No source files in {folder}')
    return texts


def check_source_hashes(raw, recorded, where):
    """The sources of a review are the files the key was made with: the same names, the same bytes."""
    if sorted(raw) != sorted(recorded):
        raise Refusal(f'{where}: the source files are not those the key was made with '
                      f'(here {sorted(raw)}, the key records {sorted(recorded)})')
    for name, data in sorted(raw.items()):
        digest = hashlib.sha256(data).hexdigest()
        if digest != recorded[name]:
            raise Refusal(f'{where}: {name} is not the file the key was made with '
                          f'(here sha256 {digest}, the key records {recorded[name]})')


def decode_sources(raw):
    return {name: data.decode('utf-8', errors='replace') for name, data in raw.items()}


def sources_folder(bundle):
    """Where the runner kept its own copy of the sources of one review."""
    return Path(bundle) / 'input' / 'sources'


# ---------- Values ----------

NONE, NULL = ('none',), ('null',)
HUGE = Decimal('Infinity')      # what an enormous integer is read as: a number that is nobody's true value
_CURRENCY = r'(?:eur|euros?|usd|gbp|pounds?|€|\$|£)'
_UNIT = r'(?:calendar days?|days?|per cent|percent|%|' + _CURRENCY[3:-1] + r')'
_NUMBER = re.compile(r'(?:\d{1,3}(?P<sep>[, \'])\d{3}(?:(?P=sep)\d{3})*|\d+)(?:\.(?P<frac>\d+))?')


def same(first, second):
    """Equality that does not take a boolean for a number."""
    if isinstance(first, bool) or isinstance(second, bool):
        return first is second
    return first == second


def form_kind(form):
    return form[0] if isinstance(form, (list, tuple)) and form else form


def form_words(form):
    return [str(word).strip().lower() for word in form[1]] if form_kind(form) == 'choice' else []


def parse_number(text, decimal):
    """A number as written in a sheet: digits with or without grouping, an optional currency sign before or a unit
    word or sign after it (the ones the report's note uses: days, calendar days, per cent, percent, %, pounds, euros,
    EUR, a currency sign), a trailing full stop. A whole number may carry at most two zeros after a point
    ("1480.00"); "500.000" is not read (it may be five hundred thousand). None when it cannot be read."""
    text = normalise(text).strip().rstrip('.').strip()
    if not text or len(text) > 60:
        return None
    text = re.sub(r'^(' + _CURRENCY + r')\s*', '', text, flags=re.I)
    text = re.sub(r'\s*(' + _UNIT + r')$', '', text, flags=re.I)
    negative = text.startswith('-')
    text = text[1:].strip() if negative else text
    match = _NUMBER.fullmatch(text)
    if not match:
        return None
    frac = match.group('frac')
    if frac and not decimal and (frac.strip('0') or len(frac) > 2):
        return None
    number = Decimal(re.sub(r"[, ']", '', text))
    return -number if negative else number


def name_key(text):
    """A name as compared: white space, quotes and case do not matter, nor a leading article, a trailing full stop."""
    key = normalise(text).strip(' .').casefold()
    return key[4:].strip() if key.startswith('the ') else key


class Names:
    """Every name the file gives each party or thing a value may name, and the reverse."""

    def __init__(self, names):
        self.names = names if isinstance(names, dict) else {}
        self.reverse = defaultdict(set)
        for key, listed in self.names.items():
            for name in (listed if isinstance(listed, list) else []) + [key]:
                if isinstance(name, str):
                    self.reverse[name_key(name)].add(key)


def read_value(raw, form, names):
    """What the agent's value says: NONE, NULL, ('ok', value) or None when it cannot be read in its form."""
    if raw is None:
        return NULL
    kind = form_kind(form)
    if isinstance(raw, bool) or isinstance(raw, (list, dict)):
        return None
    if isinstance(raw, int) and raw.bit_length() > HUGE_BITS:
        return ('ok', HUGE) if kind in ('integer', 'decimal') else None
    if isinstance(raw, str):
        text = normalise(raw[:MAX_TEXT])
        word = text.strip(' .').casefold()
        if word == 'none':
            return NONE
        if word == 'null':
            return NULL
    if kind in ('integer', 'decimal'):
        if isinstance(raw, (int, float)):
            if isinstance(raw, float) and not math.isfinite(raw):
                return None
            try:
                number = Decimal(repr(raw)) if isinstance(raw, float) else Decimal(raw)
            except (InvalidOperation, ValueError):
                return None
        elif isinstance(raw, str):
            number = parse_number(raw[:200], kind == 'decimal')
        else:
            return None
        if number is None or (kind == 'integer' and number != number.to_integral_value()):
            return None
        return 'ok', number
    if not isinstance(raw, str):
        return None
    text = normalise(raw[:MAX_TEXT]).strip()
    if kind in ('date', 'month'):
        text = text.rstrip('.').strip()
    if kind == 'date':
        return ('ok', text) if re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])', text) and _real_date(text) else None
    if kind == 'month':
        return ('ok', text) if re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', text) else None
    if kind == 'choice':
        word = text.strip(' .').casefold()
        return ('ok', word) if word in form_words(form) else None
    if kind == 'name':
        keys = names.reverse.get(name_key(text[:200]))
        return ('ok', frozenset(keys)) if keys else None
    return None


def _real_date(text):
    import datetime
    try:
        datetime.date.fromisoformat(text)
    except ValueError:
        return False
    return True


def agrees(read, target, form, none_value):
    """Whether a value as read is the target value (the true value, a stated value or a flawed reader's value)."""
    if read is None or target is None or read == NULL:
        return False
    if read == NONE:
        return same(target, none_value)
    if same(target, none_value) or isinstance(target, bool):
        return False
    kind, value = form_kind(form), read[1]
    try:
        if kind in ('integer', 'decimal'):
            if isinstance(target, str):
                return False
            return value == (Decimal(repr(target)) if isinstance(target, float) else Decimal(target))
        if kind == 'name':
            return isinstance(target, str) and target in value
        if kind == 'choice':
            return isinstance(target, str) and value == target.strip().lower()
        return value == target
    except (InvalidOperation, ValueError, TypeError):
        return False


# ---------- Families and keys ----------

def safe_answer(module, query, events, reader=None):
    try:
        return module.answer(query, events, reader) if reader else module.answer(query, events)
    except Exception:       # noqa: BLE001 - a family's answer never raises; if it does, nothing is established
        return None


def close(ids, deps, events):
    """The events of `ids` and everything that fixes how their paragraphs name their subject, followed to the end
    (`deps`: event id -> {'name': [ids], ...}; only `name` is followed: a target and an amount are not, since a canceller
    and what it cancels are rightly not needed together), in the order of the events."""
    closed, todo = set(ids), list(ids)
    while todo:
        for need in deps.get(todo.pop(), {}).get('name', ()):
            if need not in closed:
                closed.add(need)
                todo.append(need)
    return [event['id'] for event in events if event['id'] in closed]


DEPENDENCY_KINDS = ('name', 'amount', 'target')


def read_dependencies(module, events):
    """What the family says each event cannot be read without, by kind: {id: {'name': [ids], 'amount': [ids],
    'target': [ids]}}, each a list of ids of events of the list. A family that does not export `depends`, or whose
    answer is not of that shape, is refused."""
    if not callable(getattr(module, 'depends', None)):
        raise Refusal(f'{getattr(module, "__name__", module)} does not export depends')
    ids = {event['id'] for event in events}
    try:
        found = module.depends(events)
    except Exception as error:      # noqa: BLE001
        raise Refusal(f'depends of {getattr(module, "__name__", module)} fails: {error!r}')
    if not isinstance(found, dict) or not all(
            isinstance(event, str) and isinstance(kinds, dict) and all(
                isinstance(kinds.get(kind, []), (list, tuple)) and all(isinstance(need, str) for need in kinds.get(kind, ()))
                for kind in DEPENDENCY_KINDS)
            for event, kinds in found.items()):
        raise Refusal(f'depends of {getattr(module, "__name__", module)} is not a dict of dicts of lists of ids')
    return {event: {kind: [need for need in kinds.get(kind, ()) if need in ids] for kind in DEPENDENCY_KINDS}
            for event, kinds in found.items() if event in ids}


def analyse(module, query, events, deps=None):
    """What a statement rests on: its true value; the events whose removal from the full list changes the value or
    makes it not established (`needed_by_removal`, computed over every event); `needed`, those together with everything
    what fixes how their paragraphs name their subject (the family's `depends`, `name` kind, closed to the end); and
    the family's `relevant` list."""
    truth = safe_answer(module, query, events)
    by_removal = [event['id'] for event in events
                  if not same(safe_answer(module, query, [other for other in events if other is not event]), truth)]
    needed = close(by_removal, read_dependencies(module, events) if deps is None else deps, events)
    try:
        relevant = set(module.relevant(query, events))
    except Exception:       # noqa: BLE001
        relevant = set()
    return {'true': truth, 'needed': needed, 'needed_by_removal': by_removal, 'relevant': relevant,
            'outside': sorted(set(needed) - relevant)}


class Family:
    """One dossier's facts and the interpreter that reads them."""

    def __init__(self, module, facts, name):
        self.module, self.name = module, name
        self.events = facts['events']
        self.by_id = {event['id']: event for event in self.events}
        self.names = Names(facts.get('names'))
        self.none_value = module.NONE_VALUE
        # `about`: every event a paragraph tells or is headed with; `earns`: the events a quote of it establishes (a
        # passage entry whose role is a heading, `titles`, earns nothing: it names a thing and does not state it)
        self.about, self.earns = defaultdict(set), defaultdict(set)
        for passage in facts['passages']:
            spot = (passage['file'], passage['paragraph'])
            self.about[spot].update(passage['event_ids'])
            if passage.get('role') not in EARNS_NOTHING:
                self.earns[spot].update(passage['event_ids'])
        self.deps = read_dependencies(module, self.events)
        self.spots_of = defaultdict(list)           # event -> the paragraphs a quote of which earns it
        for spot in sorted(self.earns):
            if spot[0] not in NOT_SHOWABLE_FILES:
                for event in sorted(self.earns[spot]):
                    self.spots_of[event].append(spot)
        self.cache = {}
        self.readers = sorted(facts.get('readers') or [])

    def analysis(self, query):
        key = json.dumps(query, sort_keys=True)
        if key not in self.cache:
            self.cache[key] = analyse(self.module, query, self.events, self.deps)
        return self.cache[key]

    def unquotable(self, needed, sources):
        """The needed events that no quote can establish: none of the paragraphs that state one is long enough, or has
        words that occur in that paragraph only (it occurs word for word elsewhere). Not cached here: the answer for a
        paragraph is cached on the `Sources` that was asked, which dies with them."""
        return [event for event in needed if not any(sources.quotable(spot) for spot in self.spots_of.get(event, ()))]

    def events_of(self, ids):
        return [event for event in self.events if event['id'] in ids]


def module_path(name):
    return HERE / f'{name}.py'


def load_module(name, digest):
    """The module of a family, only when it is one of the large families and is the file that made the dossier."""
    if name not in generate_big.BIG_FAMILIES.values():
        raise Refusal(f'The key names an interpreter that is not a large family: {name!r}')
    path = module_path(name)
    if not path.is_file():
        raise Refusal(f'The interpreter {name} is not beside the checker')
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != digest:
        raise Refusal(f'The key was made by a different {name}.py: the key names sha256 {digest}, the file beside the checker has {actual}')
    module = importlib.import_module(name)
    missing = [part for part in INTERFACE if not hasattr(module, part)]
    if missing:
        raise Refusal(f'{name}.py does not export {", ".join(missing)}')
    return module, actual


def read_keys(keys_dir, loader=None):
    """Read and check the sealed keys. Returns {'dossiers': {name: {...}}, 'hashes': {...}}. Refuses a key that was
    not made by the family module it names, a key that holds a statement the sources leave open, and a key whose
    rows do not say what the interpreter says."""
    keys_dir = Path(keys_dir)
    manifest = keys_dir / blindness.MANIFEST_NAME
    if not manifest.is_file():
        raise Refusal(f'{keys_dir} is not a sealed keys folder (no {blindness.MANIFEST_NAME})')
    try:
        loaded = scorecard.load_keys(keys_dir)
    except (scorecard.ScoreError, ValueError, OSError) as error:
        raise Refusal(f'The keys cannot be read: {error}')
    found, modules = {}, {}
    for stem, key in sorted(loaded.items()):
        data = json.loads((keys_dir / f'{stem}.json').read_text(encoding='utf-8'))
        facts = data.get('facts')
        if not isinstance(facts, dict) or any(part not in facts for part in (
                'family', 'interpreter', 'interpreter_sha256', 'events', 'passages', 'names', 'sources', 'readers')):
            raise Refusal(f'{stem}: the key carries no facts (generate it with evalsuite/generate_big.py)')
        if facts['family'] != (data.get('generator') or {}).get('family'):
            raise Refusal(f'{stem}: the facts name another family than the key')
        if loader is None:
            module, digest = load_module(facts['interpreter'], facts['interpreter_sha256'])
        else:
            module, digest = loader(facts)
        modules[facts['interpreter']] = digest
        check_facts(stem, facts)
        family = Family(module, facts, facts['family'])
        rows = key['rows']
        for row in rows:
            check_row(stem, row, family)
        found[stem] = {'rows': rows, 'family': family, 'sha256': key['sha256'], 'sources': facts['sources']}
    return {'dossiers': found, 'modules': modules, 'manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest(),
            'keys_sha256': {stem: key['sha256'] for stem, key in sorted(found.items())}}


def check_facts(stem, facts):
    events, passages = facts['events'], facts['passages']
    if not isinstance(events, list) or not all(isinstance(e, dict) and isinstance(e.get('id'), str) for e in events):
        raise Refusal(f'{stem}: the facts hold no list of events with ids')
    ids = [event['id'] for event in events]
    if len(set(ids)) != len(ids):
        raise Refusal(f'{stem}: an event id is used twice')
    if not isinstance(facts['sources'], dict) or not facts['sources'] or not all(
            isinstance(name, str) and isinstance(digest, str) and re.fullmatch(r'[0-9a-f]{64}', digest)
            for name, digest in facts['sources'].items()):
        raise Refusal(f'{stem}: the facts record no hash for the source files')
    if not isinstance(facts['readers'], list) or not all(isinstance(name, str) for name in facts['readers']):
        raise Refusal(f'{stem}: the facts hold no list of readers')
    if not isinstance(passages, list):
        raise Refusal(f'{stem}: the passage map is not a list')
    for passage in passages:
        if not (isinstance(passage, dict) and isinstance(passage.get('file'), str)
                and isinstance(passage.get('paragraph'), int) and isinstance(passage.get('event_ids'), list)
                and set(passage['event_ids']) <= set(ids)):
            raise Refusal(f'{stem}: a passage does not name known events')


def check_row(stem, row, family):
    """The row of a key is one this version can judge, and the interpreter agrees with it."""
    where = f'{stem} {row.get("variant")}/{row.get("claim_id")}'
    if row['semantic_state'] not in DEFINITE:
        raise Refusal(f'{where}: the key leaves the statement open ({row["semantic_state"]}); that is out of scope')
    derivation = row.get('derivation') or {}
    if derivation.get('worlds', 1) != 1 or 'stated' not in derivation:
        raise Refusal(f'{where}: the key holds an unknown, or no stated value')
    unknown = sorted(set(row.get('lure_states') or {}) - set(family.readers))
    if unknown:
        raise Refusal(f'{where}: the key names readers the dossier does not have: {", ".join(unknown)}')
    query = row.get('query')
    if not isinstance(query, dict) or 'value_form' not in row:
        raise Refusal(f'{where}: the key row carries no query or value form')
    form = json.loads(json.dumps(family.module.value_form(query)))
    if form != row['value_form'] or form_kind(form) not in FORMS or (
            form_kind(form) == 'choice' and not form_words(form)):
        raise Refusal(f'{where}: the value form of the key is not the interpreter\'s: {row["value_form"]!r}')
    truth = safe_answer(family.module, query, family.events)
    if truth is None:
        raise Refusal(f'{where}: the interpreter does not establish the value from all events')
    verdict = 'supported' if same(truth, derivation['stated']) else 'refuted'
    if verdict != row['semantic_state']:
        raise Refusal(f'{where}: the interpreter gives {truth!r}, which makes the statement {verdict}, '
                      f'not {row["semantic_state"]}')
    for name in family.readers:
        got = safe_answer(family.module, query, family.events, name)
        reader_verdict = 'supported' if same(got, derivation['stated']) else 'refuted'
        if reader_verdict != (row.get('lure_states') or {}).get(name, row['semantic_state']):
            raise Refusal(f'{where}: reader {name} gives {got!r}, which the key does not say')


# ---------- One statement ----------

def stated_of(row):
    return row['derivation']['stated']


def flawed_readers(family, row, read, verdict):
    """The named flawed readers whose verdict or value is the agent's."""
    found = {}
    for name in family.readers:
        got = safe_answer(family.module, row['query'], family.events, name)
        how = []
        if verdict in DEFINITE and verdict != row['semantic_state'] and verdict == (
                'supported' if same(got, stated_of(row)) else 'refuted'):
            how.append('verdict')
        if read is not None and agrees(read, got, row['value_form'], family.none_value):
            how.append('value')
        if how:
            found[name] = how
    return found


def classify_quotes(evidence, sources):
    """Locate every quote of a row. Returns counts and the located (file, paragraph) set in the order met."""
    counts = Counter()
    located, seen = [], set()
    items = evidence if isinstance(evidence, list) else []
    cache = {}
    for position, item in enumerate(items):
        counts['given'] += 1
        quote = item.get('quote') if isinstance(item, dict) else None
        if position >= MAX_QUOTES or not isinstance(quote, str):
            counts['malformed'] += 1
            continue
        quote = quote[:MAX_TEXT]
        if quote not in cache:
            cache[quote] = sources.locate(quote)
        status, spot = cache[quote]
        counts[status] += 1
        if status == 'located' and spot not in seen:
            seen.add(spot)
            located.append(spot)
    return counts, located


def printable(value):
    """A value of a sheet as it is copied into the output: shortened (a string, and an integer too, which is never
    turned into digits when it is long) and safe to write (what cannot be encoded is escaped)."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value if value.bit_length() <= 64 else f'<an integer of {value.bit_length()} bits>'
    if isinstance(value, float):
        return value if math.isfinite(value) else repr(value)
    if isinstance(value, str):
        text = value if len(value) <= 200 else value[:200] + '...'
        return text.encode('utf-8', errors='backslashreplace').decode('utf-8')
    return type(value).__name__


def write_output(path, output):
    """Write the output as strict JSON, all or nothing: it is made in memory first, written beside the target and
    moved into place, and a failure leaves neither a partial file, a temporary one nor a folder this call made. A
    folder that does not exist is made."""
    path = Path(path)
    text = json.dumps(output, indent=2, ensure_ascii=True, allow_nan=False) + '\n'
    made, folder = [], path.parent
    while not folder.exists():
        made.append(folder)
        folder = folder.parent
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    written = False
    try:
        temporary.write_text(text, encoding='ascii')
        os.replace(temporary, path)
        written = True
    finally:
        if temporary.exists():
            temporary.unlink()
        if not written:
            for folder in made:
                try:
                    folder.rmdir()
                except OSError:
                    pass


def judge_statement(family, row, got, sources, state, out_of_time=False):
    """The class of one statement. `got` are the sheet rows for it; `state` is 'answered', or the way the review
    failed ('time' or 'format'); `out_of_time` says that the review ran out of time (its sheet may be partial). A
    statement with an empty needed set, or a needed event that no quote can establish, is `not_checkable` whatever the
    answer (no answer could show it, and none is to be blamed); its verdict is still read. Never raises."""
    out = classify_statement(family, row, got, sources, state, out_of_time)
    analysis = family.analysis(row['query'])
    why = None
    if not analysis['needed']:
        why = 'no event is needed'
    elif sources is not None:
        lost = family.unquotable(analysis['needed'], sources)
        if lost:
            why = 'a needed event cannot be quoted, no paragraph that states it is located: ' + ', '.join(lost[:6]) + (
                ' ...' if len(lost) > 6 else '')
    if why:
        out.update({'class': NOT_CHECKABLE, 'kind': 'basis not checkable', 'detail': why})
        for part in ('value', 'quotes', 'basis', 'flawed_readers'):
            out.pop(part, None)
    return out


def classify_statement(family, row, got, sources, state, out_of_time):
    key_verdict = row['semantic_state']
    out = {'claim_id': row['claim_id'], 'claim_kind': row.get('claim_kind'), 'key_verdict': key_verdict,
           'verdict': 'missing', 'verdict_match': False, 'class': None, 'kind': None, 'detail': ''}

    def done(label, kind, detail=''):
        out.update({'class': label, 'kind': kind, 'detail': detail})
        return out

    if state != 'answered':
        return done('unanswered', state, 'the review failed' if state == 'format' else 'the review ran out of time')
    if not got:
        return done('unanswered', 'time' if out_of_time else 'format',
                    'the review ran out of time and its sheet has no row for the statement' if out_of_time
                    else 'the sheet has no usable row for the statement')
    verdict = scorecard.aggregate(got)
    out['verdict'], out['verdict_match'] = verdict, verdict == key_verdict
    single = got[0] if len(got) == 1 else None
    form, none_value = row['value_form'], family.none_value
    read = read_value(single['value'], form, family.names) if single is not None and 'value' in single else None
    if verdict in DEFINITE and verdict != key_verdict:
        out['flawed_readers'] = flawed_readers(family, row, read, verdict)
        return done('wrong', 'reading', 'a definite verdict that is not the key\'s')
    if verdict not in DEFINITE:
        out['flawed_readers'] = flawed_readers(family, row, read, verdict)
        return done('withheld', 'reading', f'the verdict is {verdict}')
    if single is None:
        return done('verdict_only', 'format', 'repeated row')
    out['value'] = {'given': printable(single.get('value')),
                    'status': 'absent' if 'value' not in single else 'unreadable' if read is None else read[0]}
    if 'value' not in single:
        return done('verdict_only', 'format', 'no value')
    if read is None:
        return done('verdict_only', 'format', 'the value cannot be read in its form')
    analysis = family.analysis(row['query'])
    truth = analysis['true']
    if key_verdict == 'refuted' and agrees(read, stated_of(row), form, none_value):
        return done('verdict_only', 'format', 'the value repeats the stated value of a refuted statement')
    if not agrees(read, truth, form, none_value):
        out['flawed_readers'] = flawed_readers(family, row, read, verdict)
        return done('verdict_only', 'reading', 'the value is not the true value')
    out['value']['right'] = True
    counts, located = classify_quotes(single.get('evidence'), sources)
    earned = {spot: family.earns.get(spot, set()) for spot in located}
    cited = set().union(*earned.values()) if earned else set()
    needed = set(analysis['needed'])
    missing = sorted(needed - cited)
    relevant = analysis['relevant']
    # a paragraph is on the subject when it is about a relevant event (a heading is), however little it earns
    off = sum(1 for spot in located if not family.about.get(spot, set()) & relevant)
    needed_quotes = sum(1 for ids in earned.values() if ids & needed)
    overquoted = off > max(FREE_OFF_SUBJECT, needed_quotes)
    unlocated = counts['not_found'] + counts['ambiguous'] + counts['too_short'] + counts['malformed']
    cited_answer = safe_answer(family.module, row['query'], family.events_of(cited))
    answer_is_true = same(cited_answer, truth)
    sufficient = not missing and answer_is_true
    out['quotes'] = {name: counts[name] for name in (
        'given', 'located', 'not_found', 'ambiguous', 'too_short', 'malformed', 'procedure')}
    out['quotes']['paragraphs'] = len(located)
    out['basis'] = {'needed': analysis['needed'], 'needed_by_removal': analysis['needed_by_removal'], 'missing': missing, 'cited': len(cited), 'sufficient': sufficient,
                    'off_subject': off, 'needed_quotes': needed_quotes, 'overquoted': overquoted,
                    'cited_answer_is_true': answer_is_true, 'needed_outside_relevant': analysis['outside']}
    if not located:
        return done('right_not_shown', 'format', 'no quote given' if not counts['given'] else 'no quote located')
    if not sufficient:
        why = 'a needed event is not cited' if missing else 'the cited events do not give the true value'
        if unlocated:
            return done('right_not_shown', 'format', why + ' and a quote was not located')
        return done('right_not_shown', 'reading', why)
    if overquoted:
        return done('right_not_shown', 'overquoted', 'more quotes off the subject than allowed')
    return done('shown', 'none')


# ---------- Reviews ----------

def sheet_rows(sheet):
    """Sheet rows by the claim identifier the runner and the scorer use."""
    by_claim = defaultdict(list)
    for row in sheet['rows']:
        by_claim[row['claim_id'].strip()[:80]].append(row)
    return by_claim


def judge_review(family, key_rows, sheet, sources, state, out_of_time=False):
    """One review: a result for each statement of its variant. `sheet` is `run_blind.read_sheet`'s answer."""
    by_claim = sheet_rows(sheet) if state == 'answered' else {}
    return [judge_statement(family, row, by_claim.get(row['claim_id'], []), sources, state, out_of_time)
            for row in key_rows]


def findings_states(bundle):
    """claim id -> verdicts in the runner's `findings.json`, or None when the review has none."""
    path = Path(bundle) / 'findings.json'
    if not path.is_file():
        return None
    states = defaultdict(list)
    for finding in json.loads(path.read_text(encoding='utf-8')):
        states[finding['claim_id']].append(finding['semantic_state'])
    return states


def read_campaign(campaign, arm, keys):
    """The reviews of one arm: label, bundle folder, unit record."""
    base = Path(campaign) / arm
    index_path = base / 'records/index.json'
    if not index_path.is_file():
        raise Refusal(f'Arm "{arm}" has no staged review in {campaign}')
    recorded = Path(campaign) / 'campaign.json'
    if not recorded.is_file():
        raise Refusal(f'{campaign} has no campaign.json: it is not a campaign of the runner')
    try:
        manifest = json.loads(recorded.read_text(encoding='utf-8')).get('keys_manifest_sha256')
    except (ValueError, AttributeError):
        manifest = None
    if manifest != keys['manifest_sha256']:
        raise Refusal(f'The campaign was run with other keys: its campaign.json names the keys manifest {manifest}, '
                      f'the keys folder has {keys["manifest_sha256"]}')
    units = {}
    for path in sorted((Path(campaign) / 'units').glob('*.json')):
        record = json.loads(path.read_text(encoding='utf-8'))
        if record.get('arm') == arm:
            units[record.get('opaque_id')] = record
    try:
        loaded = {stem: {'rows': item['rows']} for stem, item in keys['dossiers'].items()}
        runs = scorecard.discover_runs(base / 'runs', loaded, json.loads(index_path.read_text(encoding='utf-8')))
    except scorecard.ScoreError as error:
        raise Refusal(f'The campaign does not match the keys: {error}')
    return [dict(run, unit=units.get(run['run_id'], {})) for run in runs]


def check_passage_map(family, sources, where):
    for (name, number), _ in family.about.items():
        if name not in sources.files or not 0 <= number < len(sources.files[name]):
            raise Refusal(f'{where}: the passage map names a paragraph the sources do not have ({name} {number})')


def run_check(campaign, keys_dir, arm, loader=None):
    keys = read_keys(keys_dir, loader)
    reviews, rows, warnings = [], [], []
    runs = read_campaign(campaign, arm, keys)
    # The sources of a dossier are the same in every review of it (their hashes are the key's). They are read once, from
    # the first review that kept them, and serve every review, a failed one too: whether a statement can be shown
    # does not depend on the review.
    for run in runs:
        key, folder = keys['dossiers'][run['dossier']], sources_folder(run['path'])
        if key.get('sources_obj') is None and folder.exists():
            raw = read_sources(folder)
            check_source_hashes(raw, key['sources'], run['run_id'])
            key['sources_obj'] = Sources(decode_sources(raw))
    for run in runs:
        key = keys['dossiers'][run['dossier']]
        key_rows = [row for row in key['rows'] if row['variant'] == run['variant']]
        bundle, unit = Path(run['path']), run['unit']
        findings = findings_states(bundle)
        ended = unit.get('ended')
        sheet = {'status': 'missing', 'rows': [], 'dropped': 0}
        sources = key.get('sources_obj')
        if findings is not None or sources_folder(bundle).exists():
            check_source_hashes(read_sources(sources_folder(bundle)), key['sources'], run['run_id'])
        if findings is not None:
            agent = bundle / 'agent'
            sheet = run_blind.read_sheet(agent) if agent.is_dir() and not agent.is_symlink() else sheet
            check_passage_map(key['family'], sources, run['run_id'])
            seen = {claim: [r['semantic_state'] for r in rows_] for claim, rows_ in sheet_rows(sheet).items()}
            expected = {claim: states for claim, states in findings.items()}
            if seen != expected:
                warnings.append(f'{run["run_id"]}: the sheet in the kept folder differs from findings.json')
        state = 'answered' if findings is not None and sheet['status'] == 'valid' else (
            'time' if ended == 'timeout' else 'format')
        if findings is not None and state != 'answered':
            warnings.append(f'{run["run_id"]}: the runner scored this review but its kept sheet cannot be read now')
        out_of_time = ended == 'timeout'
        results = judge_review(key['family'], key_rows, sheet, sources, state, out_of_time)
        # A review that ran out of time with a valid sheet is scored from the sheet, as the task text says.
        label = {'dossier': run['dossier'], 'variant': run['variant'], 'repeat': run['repeat'], 'run_id': run['run_id']}
        reviews.append(dict(label, ended=ended, sheet=sheet['status'], answered=state == 'answered',
                            out_of_time=out_of_time))
        rows += [dict(label, out_of_time=out_of_time, **result) for result in results]
    return build_output(keys, campaign, arm, reviews, rows, warnings)


# ---------- Output ----------

def share(count, total):
    return {'count': count, 'of': total, 'rate': count / total if total else None}


def block(rows):
    """Counts of one set of statements. Statements whose basis cannot be checked stand outside every share."""
    counted = [row for row in rows if row['class'] != NOT_CHECKABLE]
    classes = Counter(row['class'] for row in counted)
    n = len(counted)
    return {'statements': n, 'not_checkable': len(rows) - n,
            'classes': {name: classes[name] for name in CLASSES},
            'failures': {kind: sum(1 for row in counted if row['kind'] == kind) for kind in KINDS},
            'verdict_and_value_right': share(classes['right_not_shown'] + classes['shown'], n),
            'shown': share(classes['shown'], n), 'wrong': share(classes['wrong'], n),
            'verdict_match': share(sum(1 for row in counted if row['verdict_match']), n)}


def split(rows):
    """One block per key verdict, and one for both together."""
    return {'all': block(rows), 'supported': block([r for r in rows if r['key_verdict'] == 'supported']),
            'refuted': block([r for r in rows if r['key_verdict'] == 'refuted'])}


def build_output(keys, campaign, arm, reviews, rows, warnings):
    per_review = {}
    for review in reviews:
        mine = [row for row in rows if row['run_id'] == review['run_id']]
        per_review[review['run_id']] = dict(review, **split(mine))
    kinds = sorted({row['claim_kind'] or 'unknown' for row in rows})
    overall = split(rows)
    return {'schema_version': 1, 'checker': {'version': VERSION, 'sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
            'campaign': str(Path(campaign).resolve()), 'arm': arm,
            'families': keys['modules'], 'keys_manifest_sha256': keys['manifest_sha256'],
            'keys_sha256': keys['keys_sha256'], 'warnings': warnings,
            'headline': [{name: overall[part][name] for name in ('verdict_and_value_right', 'shown', 'wrong')}
                         | {'split': part} for part in ('all', 'supported', 'refuted')],
            'overall': overall, 'per_review': per_review,
            'per_kind': {kind: split([row for row in rows if (row['claim_kind'] or 'unknown') == kind]) for kind in kinds},
            'rows': rows}


def render(output):
    lines = []
    for part in output['headline']:
        text = '  '.join(f'{name} {part[name]["count"]}/{part[name]["of"]}' for name in ('verdict_and_value_right', 'shown', 'wrong'))
        lines.append(f'{part["split"]:>9}: {text}')
    return '\n'.join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--campaign', type=Path, required=True, help='The campaign folder the runner wrote')
    parser.add_argument('--keys', type=Path, required=True, help='Sealed answer keys, made with the facts')
    parser.add_argument('--arm', required=True)
    parser.add_argument('--output', type=Path, required=True, help='Where to write shown.json (answer material)')
    args = parser.parse_args(argv)
    try:
        output = run_check(args.campaign, args.keys, args.arm)
    except Refusal as error:
        print(f'Cannot check: {error}', file=sys.stderr)
        return 2
    write_output(args.output, output)
    print(render(output))
    for warning in output['warnings']:
        print(f'Warning: {warning}', file=sys.stderr)
    print(f'Written to {args.output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
