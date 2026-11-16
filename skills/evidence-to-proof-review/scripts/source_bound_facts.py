#!/usr/bin/env python3
"""Source-bound parser v1: exact document tokens, typed DAG and generated artifacts.

Public protocol entrypoints are extract_facts, make_fact and generate_facts.
Bindings are trusted caller inputs; immutable hashes come from manifest.md (the
existing Markdown audit format). Native provenance uses extraction-index.md,
extraction-segments.md and hash-bound accepted extraction-review.md decisions.

Rule/filter results remain SymbolicValue strings, never guessed Python values.
Their descendants remain symbolic where necessary; division/date arithmetic
requiring an unknown validity condition rejects. Lean declarations reconstruct
DAG operations from references, including concrete operations, so regeneration
also works after guard perturbations. No authored Lean is read or executed here.

The optional read_table helper reads ALL rows of one strict Markdown table. It
returns row IDs and fact references; subsets must subsequently use a scoped
filter. Count units are a deliberately finite v1 catalogue. The contract has no
unit field on ResolvedValue: dimensional checks are performed during extraction.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, replace
from datetime import date, timedelta
from fractions import Fraction
from hashlib import sha256
from pathlib import Path
import re
import sys

import audit
from source_bound_contract import (
    ByteSpan, ContractError, DerivedRecord, EnumDomain, EvidenceReference,
    FactRecord, FactsDocument, GeneratedFile, ListType, MODULE_IMPORTS, MODULE_ORDER,
    Operation, Origin, Provenance, RecordType, Reference, ResolvedFacts,
    ResolvedValue, RuleRegistration, ScalarType, SourceBinding, TypeSpec,
    build_lean_bindings, lean_enum_constructors, lean_type_binding, lean_value_name,
    parse_facts, parse_type, validate_dag,
)


class SymbolicValue(str):
    """Contract-compatible opaque string marker; it is never an enum label."""


_WORDS = {
    'en': dict(zip('zero one two three four five six seven eight nine ten eleven twelve'.split(), range(13))),
    'ro': dict(zip('zero unu doi trei patru cinci șase șapte opt nouă zece unsprezece douăsprezece'.split(), range(13)))
}
_WORDS['ro'].update({'un': 1, 'o': 1, 'două': 2})
_COUNT_UNITS = {
    'en': {'item': 'item', 'items': 'item', 'day': 'day', 'days': 'day',
           'project': 'project', 'projects': 'project', 'row': 'row', 'rows': 'row'},
    'ro': {'element': 'item', 'elemente': 'item', 'zi': 'day', 'zile': 'day',
           'proiect': 'project', 'proiecte': 'project', 'rând': 'row', 'rânduri': 'row'},
}
_CURRENCIES = {'EUR': 'EUR', 'euro': 'EUR', 'euros': 'EUR', 'RON': 'RON',
               'leu': 'RON', 'lei': 'RON', 'USD': 'USD', 'dollar': 'USD', 'dollars': 'USD'}
_MINOR = {'euro cents': 'EUR', 'cenți': 'EUR'}
_MULTIPLIERS = {'thousand': 1000, 'mie': 1000, 'mii': 1000,
                'million': 1000000, 'milion': 1000000, 'milioane': 1000000}
_PERCENT = {'%', 'percent', 'procent', 'procente'}
_MONTHS = {
    'en': 'January February March April May June July August September October November December'.split(),
    'ro': 'ianuarie februarie martie aprilie mai iunie iulie august septembrie octombrie noiembrie decembrie'.split(),
}
_SPACES = ' \u00a0'


def _fail(message):
    raise ContractError(message)


def _hash(data):
    return sha256(data).hexdigest()


def _path(root, relative):
    from source_bound_contract import _path as checked_path
    return checked_path(root, relative)


def _text(path):
    try:
        return path.read_bytes().decode('utf-8')
    except (OSError, UnicodeError) as exc:
        raise ContractError(f'Unreadable UTF-8 projection: {path}') from exc


def _rows(text):
    """Strict reader for existing readable provenance tables; no silent row loss."""
    lines = [line for line in text.splitlines() if line.startswith('|')]
    if not lines:
        return []
    parsed = []
    for line in lines:
        if not line.endswith('|'):
            _fail('Malformed provenance table row')
        parsed.append([v.strip().replace('&#124;', '|') for v in line[1:-1].split('|')])
    headers = parsed[0]
    if len(set(headers)) != len(headers):
        _fail('Duplicate provenance table column')
    result = []
    for row in parsed[1:]:
        if len(row) != len(headers):
            _fail('Malformed provenance table width')
        if all(re.fullmatch(':?-+:?', v) for v in row):
            continue
        result.append(dict(zip(headers, row)))
    return result


class _Sources:
    def __init__(self, workspace, bindings):
        self.root = Path(workspace).resolve()
        self.bindings = {b.path: b for b in bindings}
        if len(self.bindings) != len(bindings):
            _fail('Duplicate source binding')
        self.bytes = {}
        self.hashes = {}
        manifest = _rows(_text(_path(self.root, 'manifest.md')))
        if not manifest:
            _fail('Missing immutable input manifest entries')
        for row in manifest:
            if set(row) != {'Path', 'SHA256'} or row['Path'] in self.hashes:
                _fail('Malformed or duplicate manifest entry')
            data = _path(self.root, row['Path']).read_bytes()
            if not re.fullmatch('[0-9a-f]{64}', row['SHA256']) or _hash(data) != row['SHA256']:
                _fail(f'Input changed: {row["Path"]}')
            self.bytes[row['Path']] = data
            self.hashes[row['Path']] = row['SHA256']
        self.passages = {}
        for path, binding in self.bindings.items():
            if path not in self.hashes:
                _fail(f'Unbound immutable source: {path}')
            actual = _path(self.root, path).relative_to(self.root).as_posix()
            if binding.origin not in (Origin.SOURCE, Origin.REPORT):
                _fail('Unsupported source role')
            if ((binding.origin == Origin.SOURCE) != path.startswith('input/sources/') or
                    (binding.origin == Origin.SOURCE) != actual.startswith('input/sources/')):
                _fail('Origin and path disagree')
            overrides = dict(binding.anchor_locales)
            if (len(overrides) != len(binding.anchor_locales) or
                    not overrides.keys() <= binding.anchors or
                    any(v not in {'en', 'ro'} for v in overrides.values())):
                _fail('Invalid reviewed locale override')
            try:
                self.passages[path] = audit.paragraphs(self.bytes[path].decode('utf-8'))
            except (UnicodeError, audit.AuditError) as exc:
                raise ContractError(f'Unreadable or ambiguous source: {path}') from exc
        report_hashes = {self.hashes[p] for p, b in self.bindings.items() if b.origin == Origin.REPORT}
        if 'input/report.md' in self.hashes:
            report_hashes.add(self.hashes['input/report.md'])
        for path, binding in self.bindings.items():
            if binding.origin == Origin.SOURCE and self.hashes[path] in report_hashes:
                _fail('A copied report cannot serve as independent evidence')
        self.index = self.table('input/extraction-index.md', immutable=True)
        self.segments = self.table('input/extraction-segments.md', immutable=True)
        self.reviews = self.table('extraction-review.md')
        if len({r.get('Projection') for r in self.index}) != len(self.index):
            _fail('Duplicate extraction projection')
        if len({(r.get('Projection'), r.get('Anchor')) for r in self.segments}) != len(self.segments):
            _fail('Duplicate extraction segment')

    def table(self, name, immutable=False):
        if not (self.root / name).exists():
            return []
        if immutable and name not in self.hashes:
            _fail(f'Unbound extraction metadata: {name}')
        return _rows(_text(_path(self.root, name)))

    def passage(self, source, anchor):
        binding = self.bindings.get(source)
        if binding is None or anchor not in binding.anchors:
            _fail('Unbound source path or anchor')
        passage = self.passages[source].get(anchor)
        if not passage:
            _fail('Missing or unreadable source passage')
        return passage

    def evidence(self, ref, selection=None, independent=False):
        passage = self.passage(ref.source_path, ref.anchor)
        binding = self.bindings[ref.source_path]
        if independent and binding.origin != Origin.SOURCE:
            _fail('Policy/scope evidence must have source origin')
        raw, quote = passage.encode('utf-8'), ref.quote.encode('utf-8')
        if not quote or type(ref.quote_start) is not int or ref.quote_start < 0 or raw[ref.quote_start:ref.quote_start + len(quote)] != quote:
            _fail('Quote bytes do not match anchor and offset')
        original, original_hash = ref.source_path, self.hashes[ref.source_path]
        uncertainty, decision = (), 'not_required'
        indices = [r for r in self.index if r.get('Projection') == ref.source_path]
        segments = [r for r in self.segments if r.get('Projection') == ref.source_path and r.get('Anchor') == ref.anchor]
        if bool(indices) != bool(segments):
            _fail('Incomplete native passage provenance')
        if indices:
            idx, seg = indices[0], segments[0]
            original = idx.get('Original')
            original_hash = self.hashes.get(original)
            if (not original_hash or original_hash != idx.get('Original SHA256') or
                    idx.get('Projection SHA256') != self.hashes[ref.source_path] or
                    seg.get('Original') != original or seg.get('Passage SHA256') != _hash(raw) or
                    idx.get('Status') != 'ready'):
                _fail('Stale or unreadable native provenance')
            if seg.get('Review required') not in {'yes', 'no'}:
                _fail('Unknown extraction uncertainty state')
            uncertainty = (seg.get('Uncertainty', ''),) if seg['Review required'] == 'yes' else ()
            if uncertainty:
                accepted = [r for r in self.reviews if r.get('Projection') == ref.source_path and
                            r.get('Anchor') == ref.anchor and r.get('Original SHA256') == original_hash and
                            r.get('Passage SHA256') == _hash(raw)]
                if (len(accepted) != 1 or accepted[0].get('Decision') != 'accepted' or
                        not accepted[0].get('Reviewer', '').strip() or not accepted[0].get('Reason', '').strip()):
                    _fail('Uncertain native passage needs hash-bound accepted review')
                decision = 'accepted: ' + accepted[0]['Reviewer'] + ': ' + accepted[0]['Reason']
        return Provenance(ref, selection or ByteSpan(0, len(quote)), original, original_hash,
                          self.hashes[ref.source_path], _hash(raw), uncertainty, decision)


def _number(text, locale, *, signed=False):
    if locale not in _WORDS:
        _fail('Locale must be manifest-bound en or ro')
    negative = False
    if text.startswith('(') or text.endswith(')'):
        if not signed or not (text.startswith('(') and text.endswith(')')):
            _fail('Malformed negative parentheses')
        negative, text = True, text[1:-1]
    if text.startswith('-'):
        if not signed or negative:
            _fail('Malformed or duplicate negative sign')
        negative, text = True, text[1:]
    if text in _WORDS[locale]:
        value = Fraction(_WORDS[locale][text])
    else:
        decimal, group = ('.', ',') if locale == 'en' else (',', '.')
        parts = text.split(decimal)
        if len(parts) > 2 or (len(parts) == 2 and not re.fullmatch('[0-9]+', parts[1])):
            _fail('Malformed decimal or grouping')
        integer = parts[0]
        separators = [c for c in (group, ' ', '\u00a0') if c in integer]
        if len(separators) > 1:
            _fail('Mixed grouping separators')
        if separators:
            sep = separators[0]
            if not re.fullmatch('[0-9]{1,3}(?:' + re.escape(sep) + '[0-9]{3})+', integer):
                _fail('Malformed grouping')
            integer = integer.replace(sep, '')
        elif not re.fullmatch('[0-9]+', integer):
            _fail('Unreadable numeric token')
        fraction = parts[1] if len(parts) == 2 else ''
        value = Fraction(int(integer + fraction), 10 ** len(fraction))
    return -value if negative else value


def _money_context(text):
    """Return a currency, minor/major scale and optional multiplier for a header."""
    for unit, currency in {**_CURRENCIES, **_MINOR}.items():
        for mult, scale in [('', 1), *_MULTIPLIERS.items()]:
            if text in ({unit} if not mult else {f'{unit} {mult}', f'{mult} {unit}'}):
                return currency, (1 if unit in _MINOR else 100) * scale
    _fail('Unknown or ambiguous money unit context')


def _money(text, locale, unit):
    negative = False
    if text.startswith('(') or text.endswith(')'):
        if not (text.startswith('(') and text.endswith(')')):
            _fail('Malformed negative parentheses')
        negative, text = True, text[1:-1]
    if text.startswith('-'):
        if negative:
            _fail('Duplicate negative sign')
        negative, text = True, text[1:].lstrip(' ')
    contexts = []
    for spelling, currency in sorted({**_CURRENCIES, **_MINOR}.items(), key=lambda item: -len(item[0])):
        if text.startswith(spelling + ' '):
            contexts.append((currency, 1 if spelling in _MINOR else 100, text[len(spelling) + 1:]))
        if text.endswith(' ' + spelling):
            contexts.append((currency, 1 if spelling in _MINOR else 100, text[:-len(spelling) - 1]))
    if unit is not None:
        if contexts:
            _fail('Money unit supplied both inline and by header')
        currency, scale = _money_context(unit)
        contexts = [(currency, scale, text)]
    if len(contexts) != 1:
        _fail('Missing, conflicting or ambiguous currency')
    currency, scale, number = contexts[0]
    for word, multiplier in _MULTIPLIERS.items():
        if number.endswith(' ' + word):
            scale *= multiplier
            number = number[:-len(word) - 1]
            break
    # A sign next to the number (EUR -2) is also accepted, never a double sign.
    value = _number(number, locale, signed=not negative) * scale
    if negative:
        value = -value
    if value.denominator != 1:
        _fail('Fractional minor units')
    return currency, int(value)


def parse_token(token: str, type_: ScalarType, locale: str, *, unit: str | None = None,
                enum_domains: tuple[EnumDomain, ...] = ()):
    """Parse a COMPLETE selected token; returns (canonical value, dimensional unit)."""
    if not token or token != token.strip() or locale not in {'en', 'ro'}:
        _fail('Empty/padded token or unbound locale')
    if type_.kind == 'nat':
        count_unit = None
        if unit is not None:
            count_unit = _COUNT_UNITS[locale].get(unit)
            if count_unit is None:
                _fail('Unsupported count unit')
        for spelling, dimension in _COUNT_UNITS[locale].items():
            if token.endswith(' ' + spelling):
                if unit is not None:
                    _fail('Duplicate count unit')
                token, count_unit = token[:-len(spelling) - 1], dimension
                break
        multiplier = 1
        for word, scale in _MULTIPLIERS.items():
            if token.endswith(' ' + word):
                token, multiplier = token[:-len(word) - 1], scale
                break
        if token in {'un', 'o'} and count_unit is None:
            _fail('un/o requires a count unit')
        if token not in _WORDS[locale] and (('.' if locale == 'en' else ',') in token):
            _fail('nat requires unsigned integer digits or small-number words')
        value = _number(token, locale) * multiplier
        if value.denominator != 1 or value < 0:
            _fail('nat requires an unsigned integer')
        return int(value), count_unit
    if type_.kind == 'money':
        currency, value = _money(token, locale, unit)
        if currency != type_.parameter:
            _fail('Currency mismatch')
        return value, currency
    if type_.kind == 'ratio':
        if unit is not None:
            _fail('Ratio requires inline unit context')
        percent = False
        for suffix in sorted(_PERCENT, key=len, reverse=True):
            if token.endswith(suffix) and (suffix == '%' or token.endswith(' ' + suffix)):
                token, percent = token[:-len(suffix)].rstrip(' '), True
                break
        if token in {'un', 'o'}:
            _fail('un/o requires a count unit')
        return _number(token, locale, signed=True) / (100 if percent else 1), None
    if unit is not None:
        _fail('Unit context is unsupported for this type')
    if type_.kind == 'date':
        match = re.fullmatch(r'([0-9]{4})-([0-9]{2})-([0-9]{2})', token)
        if match:
            year, month, day = map(int, match.groups())
        else:
            match = re.fullmatch(r'([0-9]{2})\.([0-9]{2})\.([0-9]{4})', token) if locale == 'ro' else None
            if match:
                day, month, year = map(int, match.groups())
            else:
                match = re.fullmatch(r'([0-9]{1,2}) ([^ ]+) ([0-9]{4})', token)
                if match:
                    day, month_name, year = match.groups()
                else:
                    match = re.fullmatch(r'([^ ]+) ([0-9]{1,2}), ([0-9]{4})', token) if locale == 'en' else None
                    if not match:
                        _fail('Unsupported date spelling')
                    month_name, day, year = match.groups()
                if month_name not in _MONTHS[locale]:
                    _fail('Unknown full month name')
                month, day, year = _MONTHS[locale].index(month_name) + 1, int(day), int(year)
        try:
            return date(year, month, day), 'day'
        except ValueError as exc:
            raise ContractError('Invalid Gregorian date') from exc
    if type_.kind == 'bool':
        labels = {'true': True, 'false': False, 'yes': True, 'no': False, 'da': True, 'nu': False}
        if token.lower() not in labels:
            _fail('Unknown whole-cell Boolean')
        return labels[token.lower()], None
    if type_.kind == 'enum':
        domains = {d.id: d for d in enum_domains}
        if type_.parameter not in domains or token not in domains[type_.parameter].labels:
            _fail('Unknown exact enum label/domain')
        return token, None
    _fail('Unsupported quoted scalar type')


def _canonical(value):
    if isinstance(value, SymbolicValue):
        return str(value)
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Fraction):
        return f'{value.numerator}/{value.denominator}'
    return str(value)


def _selected(record, passage):
    raw = record.quote.encode('utf-8')
    span = record.selection
    if type(span.start) is not int or type(span.end) is not int or not 0 <= span.start < span.end <= len(raw):
        _fail('Select outside quote or empty')
    try:
        raw[:span.start].decode('utf-8')
        token = raw[span.start:span.end].decode('utf-8')
    except UnicodeError as exc:
        raise ContractError('Select splits UTF-8') from exc
    # Inspect the passage, not just Quote: shrinking Quote cannot bypass boundaries.
    start = record.quote_start + span.start
    end = record.quote_start + span.end
    whole = passage.encode('utf-8')
    before, after = whole[:start].decode('utf-8'), whole[end:].decode('utf-8')
    if ((before and (before[-1].isalnum() or before[-1] == '_')) or
            (after and (after[0].isalnum() or after[0] == '_'))):
        _fail('Selection is inside an identifier or larger token')
    if record.type.kind in {'nat', 'money', 'ratio', 'date'}:
        if ((before and before[-1] in '/.,') or
                (before.rstrip(_SPACES) and before.rstrip(_SPACES)[-1] in '+-(−–—') or
                (after and after[0] in '/%…–—') or
                re.match(r'[-.,][0-9.]', after) or after.startswith('..') or
                (after.startswith('-') and (len(after) == 1 or after[1].isalnum()))):
            _fail('Selection splits a number, date, range or signed token')
        if re.match(r'^[ \u00a0]*[-–—][ \u00a0]*[0-9]', after):
            _fail('Selection splits a numeric range')
        # A complete full-name date is a single typed token even with spaces.
        month_names = '|'.join(re.escape(m) for names in _MONTHS.values() for m in names)
        patterns = (r'[0-9]{4}-[0-9]{2}-[0-9]{2}', r'[0-9]{2}\.[0-9]{2}\.[0-9]{4}',
                    r'[0-9]{1,2} (?:' + month_names + r') [0-9]{4}',
                    r'(?:' + month_names + r') [0-9]{1,2}, [0-9]{4}')
        char_start, char_end = len(before), len(passage) - len(after)
        for pattern in patterns:
            for match in re.finditer(pattern, passage):
                if match.start() < char_end and char_start < match.end() and (char_start, char_end) != match.span():
                    _fail('Selection is inside a date token')
        # Grouped numbers and adjacent unit/multiplier words are indivisible.
        if re.search(r'[0-9][ \u00a0]+$', before) or re.match(r'^[ \u00a0]+[0-9]', after):
            _fail('Selection splits a grouped number')
        units = set(_CURRENCIES) | set(_MINOR) | set(_MULTIPLIERS) | _PERCENT | set(_COUNT_UNITS[record.locale])
        for word in units:
            if re.search(r'(?:^|\W)' + re.escape(word) + r'[ \u00a0]+$', before) or re.match(r'^[ \u00a0]+' + re.escape(word) + r'(?!\w)', after):
                _fail('Selection omits adjacent unit or multiplier')
        if re.match(r'^[ T][0-9]{1,2}:', after):
            _fail('Time-of-day is unsupported')
    if record.type.kind == 'bool':
        left = re.split(r'[|\r\n]', before)[-1]
        right = re.split(r'[|\r\n]', after)[0]
        if left.strip() or right.strip():
            _fail('Boolean selection must cover a whole cell')
    return token


def _fact_text(f):
    fence = '```'
    while fence in f.quote.splitlines():
        fence += '`'
    return (f'## Fact {f.id}\nName: {f.name}\nType: {_type_text(f.type)}\nOrigin: {f.origin}\n'
            f'Value: {f.asserted_value}\nSource: {f.source_path}\nAnchor: {f.anchor}\n'
            f'Quote-start: {f.quote_start}\nSelect: {f.selection.start}:{f.selection.end}\n'
            f'Locale: {f.locale}\nUnit-source: {f.unit_source.id if f.unit_source else "inline"}\n'
            f'Quote:\n{fence}\n{f.quote}\n{fence}\n')


def _type_text(t):
    if isinstance(t, ScalarType):
        return t.kind if t.parameter is None else f'{t.kind}({t.parameter})'
    if isinstance(t, ListType):
        return f'list({_type_text(t.element)})'
    return 'record(' + ','.join(f'{n}:{_type_text(v)}' for n, v in t.fields) + ')'


def _occurrence(text, token, index, label):
    positions, offset = [], 0
    if not token:
        _fail(f'Empty {label}')
    while (offset := text.find(token, offset)) >= 0:
        positions.append(offset)
        offset += 1
    if index is None:
        if len(positions) != 1:
            _fail(f'{label} must occur exactly once without an occurrence index')
        index = 0
    if type(index) is not int or not 0 <= index < len(positions):
        _fail(f'Invalid {label} occurrence index')
    return positions[index]


def make_fact(workspace: Path, *, id: str, type: ScalarType, source_path: str,
              anchor: str, quote: str, selected_token: str,
              bindings: tuple[SourceBinding, ...], occurrence: int | None = None,
              quote_occurrence: int | None = None, unit_source: Reference | None = None,
              context: FactsDocument | None = None) -> FactRecord:
    sources = _Sources(workspace, bindings)
    passage = sources.passage(source_path, anchor)
    binding = sources.bindings[source_path]
    start = _occurrence(passage, quote, quote_occurrence, 'Quote')
    selected = _occurrence(quote, selected_token, occurrence, 'Selected token')
    start, selected = len(passage[:start].encode()), len(quote[:selected].encode())
    locale = dict(binding.anchor_locales).get(anchor, binding.locale)
    f = FactRecord(id, id, type, binding.origin, '', source_path, anchor, quote, start,
                   ByteSpan(selected, selected + len(selected_token.encode())), locale, unit_source)
    if locale not in {'en', 'ro'}:
        _fail('Locale must be manifest-bound en or ro')
    sources.evidence(EvidenceReference(source_path, anchor, quote, start), f.selection)
    token = _selected(f, passage)
    context = context or FactsDocument((), ())
    unit = None
    if unit_source:
        resolved = extract_facts(workspace, context, bindings, ())
        units = {v.id: v for v in resolved.values}
        records = context.records()
        if unit_source.id not in units or not isinstance(records[unit_source.id], FactRecord):
            _fail('Unit-source requires a quoted context fact')
        u = records[unit_source.id]
        unit = _selected(u, sources.passage(u.source_path, u.anchor))
    value, _ = parse_token(token, type, locale, unit=unit, enum_domains=context.enum_domains)
    f = replace(f, asserted_value=_canonical(value))
    # Reuse the frozen wire validator for all structural checks, including IDs.
    parse_facts(_fact_text(f) + ''.join(_fact_text(c) for c in context.facts), workspace=Path(workspace),
                bindings=bindings, enum_domains=context.enum_domains)
    return f


def _unique(items):
    return tuple(dict.fromkeys(items))


def extract_facts(workspace: Path, document: FactsDocument,
                  bindings: tuple[SourceBinding, ...], rules: tuple[RuleRegistration, ...] = ()) -> ResolvedFacts:
    validate_dag(document, rules)
    sources = _Sources(workspace, bindings)
    # Direct dataclass callers receive the same structural validation as wire callers.
    parse_facts(''.join(_fact_text(f) for f in document.facts), workspace=Path(workspace),
                bindings=bindings, enum_domains=document.enum_domains)
    nodes = {n.id: n for n in (*document.facts, *document.derived, *rules, *document.scopes)}
    values, units, metadata = {}, {}, {}

    def resolve(ident):
        if ident in metadata:
            return metadata[ident]
        node = nodes[ident]
        if isinstance(node, FactRecord):
            dependencies = [resolve(node.unit_source.id)] if node.unit_source else []
            ref = EvidenceReference(node.source_path, node.anchor, node.quote, node.quote_start)
            provenance = sources.evidence(ref, node.selection)
            token = _selected(node, sources.passage(node.source_path, node.anchor))
            unit = None
            if node.unit_source:
                u = nodes[node.unit_source.id]
                unit = _selected(u, sources.passage(u.source_path, u.anchor))
            value, dimension = parse_token(token, node.type, node.locale, unit=unit, enum_domains=document.enum_domains)
            if node.asserted_value != _canonical(value):
                _fail(f'Asserted Value disagrees with parsed canonical value: {ident}')
            source_ids = (ident,) if node.origin == Origin.SOURCE else ()
            report_ids = (ident,) if node.origin == Origin.REPORT else ()
            prov = (provenance,)
        elif isinstance(node, DerivedRecord):
            refs = node.inputs + tuple(r for _, r in node.fields) + node.scope + ((node.rule,) if node.rule else ())
            dependencies = [resolve(r.id) for r in refs]
            args = [values[r.id] for r in node.inputs]
            arg_units = [units[r.id] for r in node.inputs]
            if node.operation in {Operation.RULE, Operation.FILTER} and any(d[1] for d in dependencies):
                _fail('Report-origin value cannot feed a rule')
            value, dimension = _evaluate(node, args, arg_units, values, units, {r.id: r for r in rules})
            source_ids, report_ids, prov = (), (), ()
        else:
            evidence = node.policy if isinstance(node, RuleRegistration) else node.evidence
            if not evidence or not (node.correspondence if isinstance(node, RuleRegistration) else node.rationale).strip():
                _fail('Rule/scope registration requires sourced evidence and correspondence')
            if isinstance(node, RuleRegistration):
                if len({n for n, _ in node.parameters}) != len(node.parameters):
                    _fail('Duplicate rule parameter')
                for _, t in node.parameters:
                    lean_type_binding(t, document.enum_domains)
                lean_type_binding(node.result_type, document.enum_domains)
            dependencies = [resolve(r.id) for r in node.dependencies] if isinstance(node, RuleRegistration) else []
            if any(d[1] for d in dependencies):
                _fail('Report-origin value cannot feed a rule')
            prov = tuple(sources.evidence(e, independent=True) for e in evidence)
            source_ids, report_ids = (), ()
        source_ids = tuple(sorted(set(source_ids).union(*(d[0] for d in dependencies))))
        report_ids = tuple(sorted(set(report_ids).union(*(d[1] for d in dependencies))))
        prov = _unique((*prov, *(p for d in dependencies for p in d[2])))
        metadata[ident] = (source_ids, report_ids, prov)
        if isinstance(node, (FactRecord, DerivedRecord)):
            units[ident] = dimension
            values[ident] = ResolvedValue(ident, node.type, value, source_ids, report_ids, prov)
        return metadata[ident]

    for ident in sorted(nodes):
        resolve(ident)
    resolved = ResolvedFacts(document, tuple(values[k] for k in sorted(values)), tuple(sorted(units.items())))
    build_lean_bindings(resolved)
    return resolved


def _evaluate(n, args, arg_units, values, units, rules):
    op, t = n.operation, n.type
    kinds = [a.type for a in args]
    vs = [a.value for a in args]
    symbolic = any(_symbolic(v) for v in vs)
    arity = {Operation.FIELD: 1, Operation.FILTER: 1, Operation.SUM: 1, Operation.COUNT: 1,
             Operation.ADD: 2, Operation.SUBTRACT: 2, Operation.MULTIPLY: 2,
             Operation.RATIO: 2, Operation.DATE_ADD: 2}
    if op in arity and len(args) != arity[op]:
        _fail('Wrong operation input count')
    expected_meta = {Operation.RECORD: 'fields', Operation.FIELD: 'field',
                     Operation.FILTER: 'rule_scope', Operation.RULE: 'rule'}.get(op, '')
    if ((bool(n.fields) and expected_meta != 'fields') or (n.field is not None and expected_meta != 'field') or
            (n.rule is not None and expected_meta not in {'rule', 'rule_scope'}) or
            (n.scope and expected_meta != 'rule_scope')):
        _fail('Operation metadata does not match operation')
    if op == Operation.LIST:
        if not isinstance(t, ListType) or any(k != t.element for k in kinds):
            _fail('List requires homogeneous exact types/currencies')
        if arg_units and any(u != arg_units[0] for u in arg_units):
            _fail('List unit mismatch')
        return tuple(vs), arg_units[0] if arg_units else (t.element.parameter if isinstance(t.element, ScalarType) and t.element.kind == 'money' else None)
    if op == Operation.RECORD:
        if args or not isinstance(t, RecordType) or tuple((name, values[r.id].type) for name, r in n.fields) != t.fields:
            _fail('Record fields/types do not match')
        return tuple((name, values[r.id].value) for name, r in n.fields), tuple((name, units[r.id]) for name, r in n.fields)
    if op == Operation.FIELD:
        if not isinstance(kinds[0], RecordType) or dict(kinds[0].fields).get(n.field) != t:
            _fail('Unknown or mistyped record field')
        return (SymbolicValue(n.id) if isinstance(vs[0], SymbolicValue) else dict(vs[0])[n.field],
                dict(arg_units[0]).get(n.field) if isinstance(arg_units[0], tuple) else None)
    if op in {Operation.FILTER, Operation.RULE}:
        if n.rule is None or n.rule.id not in rules:
            _fail('Unregistered rule')
        rule = rules[n.rule.id]
        if op == Operation.FILTER:
            if (not isinstance(kinds[0], ListType) or t != kinds[0] or not n.scope or
                    tuple(v for _, v in rule.parameters) != (t.element,) or rule.result_type != ScalarType('bool')):
                _fail('Filter requires a typed predicate and explicit scope citation')
            return SymbolicValue(n.id), arg_units[0]
        if kinds != [v for _, v in rule.parameters] or t != rule.result_type:
            _fail('Rule signature mismatch')
        return SymbolicValue(n.id), t.parameter if isinstance(t, ScalarType) and t.kind == 'money' else None
    if op in {Operation.SUM, Operation.COUNT}:
        if not isinstance(kinds[0], ListType):
            _fail('sum/count requires a list')
        if op == Operation.COUNT:
            if t != ScalarType('nat'):
                _fail('count returns nat')
            return SymbolicValue(n.id) if isinstance(vs[0], SymbolicValue) else len(vs[0]), None
        element = kinds[0].element
        if not isinstance(element, ScalarType) or element.kind not in {'nat', 'int', 'money', 'ratio'} or t != element:
            _fail('sum requires matching numeric type/currency')
        return SymbolicValue(n.id) if symbolic else sum(vs[0], Fraction(0) if t.kind == 'ratio' else 0), arg_units[0]
    if op == Operation.DATE_ADD:
        if kinds != [ScalarType('date'), ScalarType('nat')] or t != ScalarType('date') or arg_units[1] not in {None, 'day'}:
            _fail('date_add requires date and day count')
        if symbolic:
            _fail('Unknown date bounds: not formalized')
        try:
            return vs[0] + timedelta(days=vs[1]), 'day'
        except (ValueError, OverflowError) as exc:
            raise ContractError('date_add outside Gregorian range') from exc
    if len(args) != 2 or any(not isinstance(k, ScalarType) for k in kinds):
        _fail('Arithmetic requires two scalar inputs')
    a, b = kinds
    if op == Operation.SUBTRACT and a == b == ScalarType('date'):
        if t != ScalarType('int'):
            _fail('Date differences use Int')
        return SymbolicValue(n.id) if symbolic else (vs[0] - vs[1]).days, 'day'
    numeric = {'nat', 'int', 'money', 'ratio'}
    if a.kind not in numeric or b.kind not in numeric:
        _fail('Arithmetic requires numeric inputs')
    if op in {Operation.ADD, Operation.SUBTRACT, Operation.RATIO}:
        if a != b or arg_units[0] != arg_units[1]:
            _fail('Arithmetic currency/unit/type mismatch')
        expected = ScalarType('ratio') if op == Operation.RATIO else (ScalarType('int') if op == Operation.SUBTRACT and a.kind == 'nat' else a)
        if t != expected:
            _fail('Arithmetic result type mismatch')
        if op == Operation.RATIO:
            if _symbolic(vs[1]) or vs[1] == 0:
                _fail('Division requires a known nonzero denominator')
            return SymbolicValue(n.id) if symbolic else Fraction(vs[0]) / Fraction(vs[1]), None
        return (SymbolicValue(n.id) if symbolic else vs[0] + vs[1] if op == Operation.ADD else vs[0] - vs[1]), arg_units[0]
    if op == Operation.MULTIPLY:
        # One dimensionful operand times an unlabelled scalar; never money*money.
        if a.kind == b.kind == 'money' or (arg_units[0] is not None and arg_units[1] is not None):
            _fail('Multiplication currency/unit mismatch')
        if 'money' in (a.kind, b.kind):
            expected = a if a.kind == 'money' else b
        elif 'ratio' in (a.kind, b.kind):
            expected = ScalarType('ratio')
        elif 'int' in (a.kind, b.kind):
            expected = ScalarType('int')
        else:
            expected = ScalarType('nat')
        if t != expected:
            _fail('Multiplication result type mismatch')
        if symbolic and t.kind == 'money' and 'ratio' in (a.kind, b.kind):
            _fail('Unknown fractional minor units: not formalized')
        result = SymbolicValue(n.id) if symbolic else vs[0] * vs[1]
        if t.kind in {'nat', 'int', 'money'} and isinstance(result, Fraction):
            if result.denominator != 1:
                _fail('Fractional minor units')
            result = int(result)
        return result, arg_units[0] or arg_units[1]
    _fail('Unsupported derived operation')


def _symbolic(value):
    return isinstance(value, SymbolicValue) or (isinstance(value, tuple) and any(_symbolic(v) for v in value))


def _recompute(resolved, replacements, rules):
    """Re-evaluate values and recover units without claiming new quote agreement."""
    validate_dag(resolved.document, rules)
    records = resolved.document.records()
    original = {v.id: v for v in resolved.values}
    original_units = dict(resolved.dimensions)
    if original_units.keys() != original.keys():
        _fail('Synthetic recomputation requires extraction-owned dimensions')
    build_lean_bindings(resolved)
    if not replacements.keys() <= records.keys():
        _fail('Unknown synthetic replacement')
    values, units = {}, {}

    def visit(ident):
        if ident in values:
            return
        node = records[ident]
        old = original[ident]
        if isinstance(node, FactRecord):
            value, dimension = old.value, original_units[ident]
            if node.unit_source:
                visit(node.unit_source.id)
                header_id = node.unit_source.id
                original_unit = original[header_id].value
                unit = values[header_id].value
                if not isinstance(unit, str) or isinstance(unit, SymbolicValue):
                    _fail('Unit header must resolve to an exact quoted label')
                unit_changed = unit != original_unit
                if unit_changed and node.origin == Origin.REPORT:
                    _fail('Synthetic unit changes must keep report-origin values fixed')
                if unit_changed and node.type.kind == 'money':
                    old_currency, old_scale = _money_context(original_unit)
                    currency, scale = _money_context(unit)
                    if old_currency != currency or currency != node.type.parameter:
                        _fail('Currency mismatch')
                    scaled = Fraction(old.value * scale, old_scale)
                    if scaled.denominator != 1:
                        _fail('Fractional minor units')
                    value, dimension = int(scaled), currency
                elif unit_changed and node.type.kind == 'nat':
                    dimension = _COUNT_UNITS[node.locale].get(unit)
                    if dimension is None:
                        _fail('Unsupported count unit')
                elif unit_changed:
                    _fail('Unit context is unsupported for this type')
            value = replacements.get(ident, value)
            if ident in replacements and (node.origin != Origin.SOURCE or old.report_leaves):
                _fail('Synthetic replacement must have independent source origin')
            _literal(value, node.type, resolved.document.enum_domains)
        else:
            for ref in node.inputs + tuple(r for _, r in node.fields):
                visit(ref.id)
            value, dimension = _evaluate(node, [values[r.id] for r in node.inputs],
                                         [units[r.id] for r in node.inputs], values, units,
                                         {r.id: r for r in rules})
            if ident in replacements:
                changed = replacements[ident]
                if (node.operation != Operation.LIST or old.report_leaves or not value or
                        changed not in (value[1:], (value[0],) + value)):
                    _fail('Only remove-first/duplicate-first source list mutations are supported')
                value = changed
        units[ident] = dimension
        values[ident] = replace(old, value=value)

    for ident in sorted(records):
        visit(ident)
    return ResolvedFacts(resolved.document, tuple(values[k] for k in sorted(values)), tuple(sorted(units.items()))), units


def recompute(resolved: ResolvedFacts, replacements, rules: tuple[RuleRegistration, ...] = ()) -> ResolvedFacts:
    """C ProofEngine adapter for synthetic scalar or structural-list perturbations.

    Preserves document, types, dependency leaves and provenance. It does NOT
    re-extract, rewrite sources or certify synthetic values as quoted facts.
    """
    return _recompute(resolved, replacements, rules)[0]


def value_unit(reference: Reference, facts: ResolvedFacts,
               rules: tuple[RuleRegistration, ...] = ()):
    """B/integrator helper for units absent from frozen ResolvedValue fields.

    Returns None for dimensionless values, a currency/count unit for scalars or
    lists, and ordered (field, unit) pairs for records. Registered rule output
    dimensions beyond currency cannot be inferred from the frozen signature.
    """
    units = dict(facts.dimensions)
    if reference.id not in units:
        _fail('Unresolved unit reference')
    return units[reference.id]


def _literal(value, type_, domains):
    if isinstance(value, SymbolicValue):
        _fail('Symbolic values require a registered DAG expression')
    binding = lean_type_binding(type_, domains)
    if isinstance(type_, ListType):
        return '[' + ', '.join(_literal(v, type_.element, domains) for v in value) + ']'
    if isinstance(type_, RecordType):
        return '⟨' + ', '.join(_literal(dict(value)[n], t, domains) for n, t in type_.fields) + '⟩'
    if type_.kind in {'nat', 'int', 'money'}:
        if type(value) is not int or (type_.kind == 'nat' and value < 0):
            _fail('Invalid integer value')
        return f'⟨{value}⟩' if type_.kind == 'money' else str(value)
    if type_.kind == 'date':
        if not isinstance(value, date):
            _fail('Invalid date value')
        return f'⟨{value.toordinal()}⟩'
    if type_.kind == 'ratio':
        if not isinstance(value, Fraction):
            _fail('Invalid exact ratio value')
        return f'⟨{value.numerator}, {value.denominator}⟩'
    if type_.kind == 'bool':
        if type(value) is not bool:
            _fail('Invalid Boolean value')
        return 'true' if value else 'false'
    if type_.kind == 'enum' and value in dict(binding.constructors):
        return dict(binding.constructors)[value]
    _fail('Unsupported generated value')


def _ratio_expr(num, den):
    # Positive denominator already checked by extraction; normalize exact arithmetic.
    return (f'(let n : Int := {num}; let d : Nat := {den}; '
            'let g := Int.gcd n (Int.ofNat d); '
            f'(⟨n / Int.ofNat g, d / g⟩ : {lean_type_binding(ScalarType("ratio")).lean_type}))')


def _derived_expr(node, bindings, records):
    names = [bindings.values[r.id].name for r in node.inputs]
    types = [records[r.id].type for r in node.inputs]
    op, t = node.operation, node.type

    def integer(i):
        b = bindings.types[types[i]]
        if not b.integer_projection:
            _fail('Missing integer projection')
        return b.integer_projection.format(value=names[i])

    def ratio(i):
        if types[i] == ScalarType('ratio'):
            n, d = bindings.types[types[i]].ratio_projection
            return n.format(value=names[i]), f'Int.toNat ({d.format(value=names[i])})'
        return integer(i), '1'

    def wrap(expr):
        return f'⟨{expr}⟩' if isinstance(t, ScalarType) and t.kind in {'money', 'date'} else expr

    if op == Operation.LIST:
        return '[' + ', '.join(names) + ']'
    if op == Operation.RECORD:
        return '⟨' + ', '.join(bindings.values[r.id].name for _, r in node.fields) + '⟩'
    if op == Operation.FIELD:
        field = next(lean for wire, lean, _ in bindings.types[types[0]].fields if wire == node.field)
        return f'({names[0]}).{field}'
    if op == Operation.RULE:
        return 'Rules.' + node.rule.id + ''.join(f' ({name})' for name in names)
    if op == Operation.FILTER:
        return f'({names[0]}).filter Rules.{node.rule.id}'
    if op == Operation.COUNT:
        return f'({names[0]}).length'
    if op == Operation.SUM:
        if t.kind == 'ratio':
            rb = bindings.types[t]
            num, den = rb.ratio_projection
            an, ad = num.format(value='a'), den.format(value='a')
            bn, bd = num.format(value='b'), den.format(value='b')
            body = _ratio_expr(f'{an} * ({bd}) + {bn} * ({ad})', f'Int.toNat ({ad}) * Int.toNat ({bd})')
            return f'({names[0]}).foldl (fun (a b : {rb.lean_type}) => {body}) ⟨0, 1⟩'
        projection = bindings.types[t].integer_projection
        if t.kind == 'nat':
            return f'({names[0]}).foldl (fun a b => a + b) 0'
        return wrap(f'({names[0]}).foldl (fun a b => a + {projection.format(value="b")}) (0 : Int)')
    if op == Operation.DATE_ADD:
        return wrap(f'{integer(0)} + {integer(1)}')
    if t == ScalarType('ratio'):
        an, ad = ratio(0)
        bn, bd = ratio(1)
        if op in {Operation.ADD, Operation.SUBTRACT}:
            sign = '+' if op == Operation.ADD else '-'
            return _ratio_expr(f'({an}) * Int.ofNat ({bd}) {sign} ({bn}) * Int.ofNat ({ad})', f'({ad}) * ({bd})')
        if op == Operation.MULTIPLY:
            return _ratio_expr(f'({an}) * ({bn})', f'({ad}) * ({bd})')
        # Division also normalizes a negative divisor into a signed numerator.
        return _ratio_expr(f'(if ({bn}) < 0 then -({an}) else ({an})) * Int.ofNat ({bd})', f'({ad}) * ({bn}).natAbs')
    if op == Operation.MULTIPLY and ScalarType('ratio') in types:
        ri = types.index(ScalarType('ratio'))
        mi = 1 - ri
        rn, rd = ratio(ri)
        return wrap(f'({integer(mi)} * ({rn})) / Int.ofNat ({rd})')
    symbol = {Operation.ADD: '+', Operation.SUBTRACT: '-', Operation.MULTIPLY: '*'}[op]
    if t == ScalarType('nat'):
        return f'{names[0]} {symbol} {names[1]}'
    return wrap(f'{integer(0)} {symbol} {integer(1)}')


def _table(headers, rows):
    def escape(v):
        return str(v).replace('&', '&amp;').replace('|', '&#124;').replace('<', '&lt;').replace('>', '&gt;').replace('\r', '&#13;').replace('\n', '<br>')
    return '\n'.join(['| ' + ' | '.join(headers) + ' |', '| ' + ' | '.join('---' for _ in headers) + ' |'] +
                     ['| ' + ' | '.join(escape(v) for v in row) + ' |' for row in rows]) + '\n'


def generate_facts(resolved: ResolvedFacts, rules: tuple[RuleRegistration, ...] = ()) -> tuple[GeneratedFile, ...]:
    """Return Types/Facts/Derived and provenance.md; never write to the workspace."""
    validate_dag(resolved.document, rules)
    bindings = build_lean_bindings(resolved)
    records, values = resolved.document.records(), {v.id: v for v in resolved.values}
    domains = resolved.document.enum_domains
    facts = ['-- Generated source-bound facts; quote text is retained only in Markdown.']
    facts += ['import ' + m for m in MODULE_IMPORTS['SourceBound.Facts']]
    emitted = set()

    def emit_type(t):
        if t in emitted:
            return
        emitted.add(t)
        if isinstance(t, ListType):
            emit_type(t.element)
        elif isinstance(t, RecordType):
            for _, child in t.fields:
                emit_type(child)
            b = lean_type_binding(t, domains)
            facts.append(f'structure {b.lean_type} where')
            facts.extend(f'  {lean} : {kind}' for _, lean, kind in b.fields)
            facts.append('  deriving DecidableEq, Repr')
        elif t.kind == 'enum':
            b = lean_type_binding(t, domains)
            facts.append(f'inductive {b.lean_type} where')
            facts.extend('  | ' + name.rsplit('.', 1)[1] for _, name in b.constructors)
            facts.append('  deriving DecidableEq, Repr')

    for t in bindings.types:
        emit_type(t)
    for rule in sorted(rules, key=lambda r: r.id):
        for _, t in rule.parameters:
            emit_type(t)
        emit_type(rule.result_type)
    for f in sorted(resolved.document.facts, key=lambda f: f.id):
        b = bindings.values[f.id]
        facts.append(f'def {b.name} : {b.lean_type} := {_literal(values[f.id].value, f.type, domains)}')
    derived = ['-- Generated typed dependency graph.'] + ['import ' + m for m in MODULE_IMPORTS['SourceBound.Derived']]
    done = set()

    def emit(n):
        if n.id in done:
            return
        done.add(n.id)
        for ref in n.inputs + tuple(r for _, r in n.fields):
            if isinstance(records[ref.id], DerivedRecord):
                emit(records[ref.id])
        b = bindings.values[n.id]
        expression = _derived_expr(n, bindings, records)
        if n.operation == Operation.LIST:
            expected = tuple(values[r.id].value for r in n.inputs)
            actual = values[n.id].value
            if actual != expected:
                if expected and actual == expected[1:]:
                    expression = f'({expression}).drop 1'
                elif expected and actual == (expected[0],) + expected:
                    expression = f'{bindings.values[n.inputs[0].id].name} :: ({expression})'
                else:
                    _fail('Unrecognized structural list replacement')
        derived.append(f'def {b.name} : {b.lean_type} := {expression}')

    for n in sorted(resolved.document.derived, key=lambda n: n.id):
        emit(n)
    synthetic = any(isinstance(n, FactRecord) and _canonical(values[k].value) != n.asserted_value
                    for k, n in records.items())
    summary = []
    for ident, record in sorted(records.items()):
        v = values[ident]
        summary.append((ident, _type_text(record.type), str(record.origin) if isinstance(record, FactRecord) else 'derived',
                        _canonical(v.value), ', '.join(v.source_leaves), ', '.join(v.report_leaves),
                        ', '.join(r.id for r in record.inputs) if isinstance(record, DerivedRecord) else ''))
    provenance = []
    for ident in sorted(values):
        for p in values[ident].provenance:
            e = p.evidence
            provenance.append((ident, e.source_path + '#' + e.anchor, e.quote_start,
                f'{p.selection.start}:{p.selection.end}', e.quote, p.original_path, p.original_sha256,
                p.projection_sha256, p.passage_sha256, '; '.join(p.uncertainty), p.uncertainty_decision))
    summary_text = '# Source-bound provenance\n\nParser: source-bound-parser-v1\n\n'
    if synthetic or any(n.operation == Operation.LIST and values[n.id].value != tuple(values[r.id].value for r in n.inputs)
                        for n in resolved.document.derived):
        summary_text += 'SYNTHETIC perturbation: changed values do not assert quote agreement.\n\n'
    summary_text += _table(('ID', 'Type', 'Origin', 'Value', 'Source leaves', 'Report leaves', 'Inputs'), summary)
    summary_text += '\n' + _table(('ID', 'Source', 'Quote-start', 'Select', 'Quote', 'Original', 'Original SHA256',
                                    'Projection SHA256', 'Passage SHA256', 'Uncertainty', 'Decision'), provenance)
    modules = {
        'SourceBound.Types': _text(Path(__file__).resolve().parents[1] / 'assets/source_bound/Types.lean'),
        'SourceBound.Facts': '\n'.join(facts) + '\n',
        'SourceBound.Derived': '\n'.join(derived) + '\n',
    }
    return tuple(GeneratedFile('models/' + m.replace('.', '/') + '.lean', modules[m])
                 for m in MODULE_ORDER if m in modules) + (GeneratedFile('source-bound/provenance.md', summary_text),)


@dataclass(frozen=True)
class TableRead:
    facts: tuple[FactRecord, ...]
    rows: tuple[tuple[str, Reference], ...]
    count: int
    header: EvidenceReference
    list_record: DerivedRecord


def read_table(workspace: Path, *, source_path: str, anchor: str, column: str,
               row_id_column: str, id_prefix: str, type: ScalarType,
               bindings: tuple[SourceBinding, ...], context: FactsDocument | None = None,
               unit_source: Reference | None = None) -> TableRead:
    """Read every row of one bound, pipe-delimited Markdown table, in order.

    Escaped pipes, multiline cells, duplicate row IDs, extra tables and empty
    cells reject. A subset is not an input option: use a scoped derived filter.
    """
    sources = _Sources(workspace, bindings)
    passage = sources.passage(source_path, anchor)
    lines = passage.splitlines(keepends=True)
    if any(line.strip() and not line.startswith('|') for line in lines):
        _fail('Unsupported content or row syntax in anchored table')
    table_lines = [(i, line.rstrip('\r\n')) for i, line in enumerate(lines) if line.startswith('|')]
    if len(table_lines) < 2 or [i for i, _ in table_lines] != list(range(table_lines[0][0], table_lines[-1][0] + 1)):
        _fail('Expected one contiguous complete table')
    cells = []
    for _, line in table_lines:
        if not line.endswith('|') or '\\' in line or '&#' in line:
            _fail('Unsupported table cell encoding')
        cells.append([part.strip() for part in line[1:-1].split('|')])
    headers = cells[0]
    if len(set(headers)) != len(headers) or column not in headers or row_id_column not in headers:
        _fail('Unknown or duplicate table header')
    if len(cells[1]) != len(headers) or not all(re.fullmatch(':?-{3,}:?', v) for v in cells[1]):
        _fail('Malformed table delimiter')
    if any(len(row) != len(headers) or any(not v for v in row) for row in cells[2:]):
        _fail('Missing or malformed table cell')
    index, ri = headers.index(column), headers.index(row_id_column)
    rows, facts = [], []
    row_ids = [row[ri] for row in cells[2:]]
    if len(set(row_ids)) != len(row_ids):
        _fail('Duplicate table row ID')
    for n, row in enumerate(cells[2:]):
        line_index, line = table_lines[n + 2]
        parts = line.split('|')
        token = row[index]
        column_start = sum(len(v) + 1 for v in parts[:index + 1])
        cell_start = column_start + len(parts[index + 1]) - len(parts[index + 1].lstrip())
        token_occurrences, position = [], 0
        while (position := line.find(token, position)) >= 0:
            token_occurrences.append(position)
            position += 1
        occurrence = token_occurrences.index(cell_start)
        # Explicit quote index disambiguates identical rows; duplicate IDs already fail.
        line_start = sum(len(s) for s in lines[:line_index])
        quote_positions, off = [], 0
        while (off := passage.find(line, off)) >= 0:
            quote_positions.append(off)
            off += 1
        f = make_fact(workspace, id=f'{id_prefix}_{n + 1}', type=type, source_path=source_path,
                      anchor=anchor, quote=line, selected_token=token, bindings=bindings,
                      occurrence=occurrence, quote_occurrence=quote_positions.index(line_start),
                      unit_source=unit_source, context=context)
        facts.append(f)
        rows.append((row[ri], Reference(f.id)))
    header_line_index, header = table_lines[0]
    header_offset = len(''.join(lines[:header_line_index]).encode())
    return TableRead(tuple(facts), tuple(rows), len(rows), EvidenceReference(source_path, anchor, header, header_offset),
                     DerivedRecord(id_prefix, ListType(type), Operation.LIST, tuple(r for _, r in rows)))


def main(argv=None):
    parser = argparse.ArgumentParser(description='Compute a source-bound fact and UTF-8 offsets from immutable input.')
    parser.add_argument('workspace', type=Path)
    parser.add_argument('--id', required=True)
    parser.add_argument('--type', required=True)
    parser.add_argument('--source', required=True)
    parser.add_argument('--anchor', required=True)
    parser.add_argument('--quote', required=True)
    parser.add_argument('--token', required=True)
    parser.add_argument('--occurrence', type=int)
    parser.add_argument('--quote-occurrence', type=int)
    parser.add_argument('--bindings', required=True, type=Path,
                        help='Trusted manifest-side Markdown table: Path | Origin | Locale | Anchors; optional Anchor locales (anchor=locale,...)')
    args = parser.parse_args(argv)
    try:
        bindings = []
        for row in _rows(_text(args.bindings)):
            if set(row) not in ({'Path', 'Origin', 'Locale', 'Anchors'}, {'Path', 'Origin', 'Locale', 'Anchors', 'Anchor locales'}):
                _fail('Malformed binding table')
            overrides = tuple(tuple(p.strip().split('=')) for p in row.get('Anchor locales', '').split(',') if p.strip())
            if any(len(p) != 2 for p in overrides):
                _fail('Malformed anchor locale override')
            bindings.append(SourceBinding(row['Path'], Origin(row['Origin']), row['Locale'],
                                          frozenset(s.strip() for s in row['Anchors'].split(',')), overrides))
        t = parse_type(args.type)
        if not isinstance(t, ScalarType):
            _fail('make_fact requires a scalar type')
        result = make_fact(args.workspace, id=args.id, type=t, source_path=args.source,
                           anchor=args.anchor, quote=args.quote, selected_token=args.token,
                           bindings=tuple(bindings), occurrence=args.occurrence, quote_occurrence=args.quote_occurrence)
        print(_fact_text(result), end='')
        return 0
    except (ContractError, ValueError, OSError) as exc:
        print(f'make_fact: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
